from django import template
from django.shortcuts import resolve_url
from django.template import Variable
from django.template.defaulttags import token_kwargs
from django.template.exceptions import TemplateSyntaxError
from django.template.loader import render_to_string
from django.templatetags.cache import CacheNode as DjangoCacheNode
from django.utils.encoding import force_str
from django.utils.html import conditional_escape

from wagtail import VERSION, __version__
from wagtail.models import PAGE_TEMPLATE_VAR, Page, Site
from wagtail.rich_text import RichText, expand_db_html
from wagtail.utils.version import get_main_version

register = template.Library()


@register.simple_tag(takes_context=True)
def pageurl(context, page, fallback=None):
    """
    Outputs a page's URL as relative (/foo/bar/) if it's within the same site as the
    current page, or absolute (http://example.com/foo/bar/) if not.
    If kwargs contains a fallback view name and page is None, the fallback view url will be returned.
    """
    if page is None and fallback:
        return resolve_url(fallback)

    if not isinstance(page, Page):
        raise ValueError("pageurl tag expected a Page object, got %r" % page)

    return page.get_url(request=context.get("request"))


@register.simple_tag(takes_context=True)
def fullpageurl(context, page, fallback=None):
    """
    Outputs a page's absolute URL (http://example.com/foo/bar/)
    If kwargs contains a fallback view name and page is None, the fallback view url will be returned.
    """
    if page is None and fallback:
        fallback_url = resolve_url(fallback)
        if fallback_url and "request" in context and fallback_url[0] == "/":
            fallback_url = context["request"].build_absolute_uri(fallback_url)
        return fallback_url

    if not isinstance(page, Page):
        raise ValueError("fullpageurl tag expected a Page object, got %r" % page)

    return page.get_full_url(request=context.get("request"))


@register.simple_tag(takes_context=True)
def slugurl(context, slug):
    """
    Returns the URL for the page that has the given slug.

    First tries to find a page on the current site. If that fails or a request
    is not available in the context, then returns the URL for the first page
    that matches the slug on any site.
    """

    page = None
    try:
        site = Site.find_for_request(context["request"])
        current_site = site
    except KeyError:
        # No site object found - allow the fallback below to take place.
        pass
    else:
        if current_site is not None:
            page = Page.objects.in_site(current_site).filter(slug=slug).first()

    # If no page is found, fall back to searching the whole tree.
    if page is None:
        page = Page.objects.filter(slug=slug).first()

    if page:
        # call pageurl() instead of page.relative_url() here so we get the ``accepts_kwarg`` logic
        return pageurl(context, page)


@register.simple_tag
def wagtail_version():
    return __version__


@register.simple_tag
def wagtail_documentation_path():
    major, minor, patch, release, num = VERSION
    if release == "final":
        return "https://docs.wagtail.org/en/v%s" % __version__
    else:
        return "https://docs.wagtail.org/en/latest"


@register.simple_tag
def wagtail_release_notes_path():
    return "%s.html" % get_main_version(VERSION)


@register.simple_tag
def wagtail_feature_release_whats_new_link():
    return "https://guide.wagtail.org/en-latest/releases/latest/"


@register.simple_tag
def wagtail_feature_release_editor_guide_link():
    return "https://guide.wagtail.org/"


@register.filter
def richtext(value):
    if isinstance(value, RichText):
        # passing a RichText value through the |richtext filter should have no effect
        return value
    elif value is None:
        html = ""
    else:
        if isinstance(value, str):
            html = expand_db_html(value)
        else:
            raise TypeError(
                "'richtext' template filter received an invalid value; expected string, got {}.".format(
                    type(value)
                )
            )
    return render_to_string("wagtailcore/shared/richtext.html", {"html": html})


class IncludeBlockNode(template.Node):
    def __init__(self, block_var, extra_context, use_parent_context):
        self.block_var = block_var
        self.extra_context = extra_context
        self.use_parent_context = use_parent_context

    def render(self, context):
        try:
            value = self.block_var.resolve(context)
        except template.VariableDoesNotExist:
            return ""

        if hasattr(value, "render_as_block"):
            if self.use_parent_context:
                new_context = context.flatten()
            else:
                new_context = {}

            if self.extra_context:
                for var_name, var_value in self.extra_context.items():
                    new_context[var_name] = var_value.resolve(context)

            output = value.render_as_block(context=new_context)
        else:
            output = value

        if context.autoescape:
            return conditional_escape(output)
        else:
            return force_str(output)


@register.tag
def include_block(parser, token):
    """
    Render the passed item of StreamField content, passing the current template context
    if there's an identifiable way of doing so (i.e. if it has a `render_as_block` method).
    """
    tokens = token.split_contents()

    try:
        tag_name = tokens.pop(0)
        block_var_token = tokens.pop(0)
    except IndexError:
        raise template.TemplateSyntaxError(
            "%r tag requires at least one argument" % tag_name
        )

    block_var = parser.compile_filter(block_var_token)

    if tokens and tokens[0] == "with":
        tokens.pop(0)
        extra_context = token_kwargs(tokens, parser)
    else:
        extra_context = None

    use_parent_context = True
    if tokens and tokens[0] == "only":
        tokens.pop(0)
        use_parent_context = False

    if tokens:
        raise template.TemplateSyntaxError(
            "Unexpected argument to %r tag: %r" % (tag_name, tokens[0])
        )

    return IncludeBlockNode(block_var, extra_context, use_parent_context)


@register.simple_tag(takes_context=True)
def wagtail_site(context):
    """
    Returns the Site object for the given request
    """
    try:
        request = context["request"]
    except KeyError:
        return None

    return Site.find_for_request(request=request)


class WagtailCacheNode(DjangoCacheNode):
    """
    A modified version of Django's `CacheNode` which is aware of Wagtail's
    page previews.
    """

    def render(self, context):
        try:
            request = context["request"]
        except KeyError:
            # When there's no request, it's not possible to tell whether this is a preview or not.
            # Bypass the cache to be safe.
            return self.nodelist.render(context)

        if getattr(request, "is_preview", False):
            # Skip cache in preview
            return self.nodelist.render(context)

        return super().render(context)


class WagtailPageCacheNode(WagtailCacheNode):
    """
    A modified version of Django's `CacheNode` designed for caching fragments
    of pages.

    This tag intentionally makes assumptions about what context is available.
    If these assumptions aren't valid, it's recommended to just use `{% wagtailcache %}`.
    """

    CACHE_SITE_TEMPLATE_VAR = "wagtail_page_cache_site"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Pretend the user specified the page and site as part of context
        self.vary_on.extend(
            [
                Variable(f"{PAGE_TEMPLATE_VAR}.cache_key"),
                Variable(f"{self.CACHE_SITE_TEMPLATE_VAR}.pk"),
            ]
        )

    def render(self, context):
        if "request" in context:
            # Inject the site into context to be picked up when resolving `vary_on`
            with context.update(
                {
                    self.CACHE_SITE_TEMPLATE_VAR: Site.find_for_request(
                        context["request"]
                    )
                }
            ):
                return super().render(context)
        return super().render(context)


def register_cache_tag(tag_name, node_class):
    """
    A helper function to define cache tags without duplicating `do_cache`.
    """

    @register.tag(tag_name)
    def do_cache(parser, token):
        # Implementation copied from `django.templatetags.cache.do_cache`
        nodelist = parser.parse((f"end{tag_name}",))
        parser.delete_first_token()
        tokens = token.split_contents()
        if len(tokens) < 3:
            raise TemplateSyntaxError(
                f"'{tokens[0]}' tag requires at least 2 arguments."
            )
        if len(tokens) > 3 and tokens[-1].startswith("using="):
            cache_name = parser.compile_filter(tokens[-1][len("using=") :])
            tokens = tokens[:-1]
        else:
            cache_name = None
        return node_class(
            nodelist,
            parser.compile_filter(tokens[1]),
            tokens[2],  # fragment_name can't be a variable.
            [parser.compile_filter(t) for t in tokens[3:]],
            cache_name,
        )


register_cache_tag("wagtailcache", WagtailCacheNode)
register_cache_tag("wagtailpagecache", WagtailPageCacheNode)
