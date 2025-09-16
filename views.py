import requests

from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib import messages
from django.utils import timezone
from django.http import Http404
from django.utils.translation import gettext_lazy as _
from django.db.models import Q

from submission import models, forms
from submission.logic import add_new_author_from_form, get_credit_form
from core import models as core_models
from plugins.back_content import forms as bc_forms, logic as bc_logic, plugin_settings
from production import logic as prod_logic, forms as prod_forms
from identifiers import logic as id_logic
from security.decorators import editor_user_required
from journal import logic as journal_logic
from events import logic as event_logic


@editor_user_required
def index(request):
    if request.POST:
        article = models.Article.objects.create(
            journal=request.journal,
            date_accepted=timezone.now(),
            is_import=True,
            article_agreement='This record was created using the back '
                              'content plugin.',
        )
        return redirect(reverse('bc_article', kwargs={'article_id': article.pk}))

    articles = models.Article.objects.filter(
        journal=request.journal,
    ).select_related(
        'correspondence_author',
        'section',
    )

    template = 'back_content/new_article.html'
    context = {
        'articles': articles,
    }

    return render(request, template, context)


@editor_user_required
def article(request, article_id):
    article = get_object_or_404(
        models.Article,
        pk=article_id,
        journal=request.journal,
    )
    additional_fields = models.Field.objects.filter(
        journal=request.journal,
    )
    article_form = bc_forms.ArticleInfo(
        instance=article,
        additional_fields=additional_fields,
    )
    author_form = forms.EditFrozenAuthor()
    pub_form = bc_forms.PublicationInfo(instance=article)
    remote_form = bc_forms.RemoteArticle(instance=article)
    galley_form = prod_forms.GalleyForm()
    modal = None

    if request.POST:
        if 'save_section_1' in request.POST:
            article_form = bc_forms.ArticleInfo(
                request.POST,
                instance=article,
                additional_fields=additional_fields,
            )

            if article_form.is_valid():
                article_form.save()
                return bc_logic.return_url(
                    article,
                    section='section-one',
                )

        if 'save_section_2' in request.POST:
            correspondence_author = request.POST.get('main-author', None)

            if correspondence_author:
                author = core_models.Account.objects.get(pk=correspondence_author)
                article.correspondence_author = author
                article.save()
                return bc_logic.return_url(
                    article,
                    section='section-two',
                )

        if 'save_section_4' in request.POST:
            pub_form = bc_forms.PublicationInfo(request.POST, instance=article)

            if pub_form.is_valid():
                pub_form.save()
                if article.primary_issue:
                    article.primary_issue.articles.add(article)

                if article.date_published:
                    article.stage = models.STAGE_READY_FOR_PUBLICATION
                    article.save()
                return bc_logic.return_url(
                    article,
                    section='section-four',
                )

        if 'save_section_5' in request.POST:
            remote_form = bc_forms.RemoteArticle(request.POST, instance=article)

            if remote_form.is_valid():
                remote_form.save()
                return bc_logic.return_url(
                    article,
                    section='section-five',
                )

        if 'file' in request.FILES:
            label = request.POST.get('label')
            for uploaded_file in request.FILES.getlist('file'):
                prod_logic.save_galley(
                    article,
                    request,
                    uploaded_file,
                    is_galley=True,
                    label=label,
                )
            return bc_logic.return_url(
                article,
                section='section-three',
            )

        if 'set_main' in request.POST:
            account = get_object_or_404(
                core_models.Account,
                pk=request.POST.get('set_main', None),
                frozenauthor__article=article,
            )
            article.correspondence_author = account
            article.save()
            messages.add_message(
                request,
                messages.SUCCESS,
                _('%(author_name)s (%(email)s) made correspondence author.')
                % {
                    "author_name": account.full_name(),
                    "email": account.email
                },
            )

        if request.POST and 'add_author' in request.POST:
            add_new_author_from_form(
                request,
                article,
            )

            return bc_logic.return_url(
                article,
                section='section-two',
            )

        if 'remove_author' in request.POST:
            author_pk = request.POST.get('remove_author', None)
            if author_pk:
                author_to_remove = get_object_or_404(
                    models.FrozenAuthor,
                    pk=author_pk,
                )
                author_to_remove.delete()
                if author_to_remove.author == article.correspondence_author:
                    article.correspondence_author = None
                    article.save()
                messages.success(
                    request,
                    f'{author_to_remove} removed from article.',
                )
            else:
                messages.warning(
                    request,
                    f'No author ID provided.',
                )
            return bc_logic.return_url(
                article,
                section='section-two',
            )

        if 'publish' in request.POST:
            crossref_enabled = request.journal.get_setting(
                'Identifiers',
                'use_crossref',
            )
            if not article.stage == models.STAGE_PUBLISHED:
                if crossref_enabled:
                    id_logic.generate_crossref_doi_with_pattern(article)
                article.stage = models.STAGE_PUBLISHED
                article.snapshot_authors(article)
                article.save()

            if plugin_settings.IS_WORKFLOW_PLUGIN:
                workflow_kwargs = {'handshake_url': 'bc_article',
                                   'request': request,
                                   'article': article,
                                   'switch_stage': True}
                return event_logic.Events.raise_event(event_logic.Events.ON_WORKFLOW_ELEMENT_COMPLETE,
                                                      task_object=article,
                                                      **workflow_kwargs)
            else:
                return redirect(reverse('bc_index'))

    authors = []
    for author, credits in article.authors_and_credits().items():
        credit_form = get_credit_form(request, author)
        authors.append((author, credits, credit_form))

    template = 'back_content/article.html'
    context = {
        'article': article,
        'article_form': article_form,
        'form': author_form,
        'pub_form': pub_form,
        'galleys': prod_logic.get_all_galleys(article),
        'remote_form': remote_form,
        'modal': modal,
        'galley_form': galley_form,
        'additional_fields': additional_fields,
        'authors': authors,
    }

    return render(request, template, context)


@editor_user_required
def doi_import(request):
    form = bc_forms.RemoteParse()

    if request.POST:
        form = bc_forms.RemoteParse(request.POST)
        if form.is_valid():
            url = form.cleaned_data['url']
            mode = form.cleaned_data['mode']

            if mode == 'doi':
                r = requests.get('https://api.crossref.org/v1/works/{0}'.format(url)).json()
                article = bc_logic.get_and_parse_doi_metadata(r, request, doi=url)
                return redirect(reverse('bc_article', kwargs={'article_id': article.pk}))
            else:
                r = requests.get(url)
                article = bc_logic.parse_url_results(r, request)
                return redirect(reverse('bc_article', kwargs={'article_id': article.pk}))

    template = 'back_content/doi_import.html'
    context = {
        'form': form,
    }

    return render(request, template, context)


@editor_user_required
def preview_xml_galley(request, article_id, galley_id):
    """
    Allows an editor to preview an article's XML galleys.
    :param request: HttpRequest
    :param article_id: Article object ID (INT)
    :param galley_id: Galley object ID (INT)
    :return: HttpResponse
    """

    article = get_object_or_404(models.Article, journal=request.journal, pk=article_id)
    galley = core_models.Galley.objects.filter(article=article, file__mime_type__contains='/xml', pk=galley_id)

    if not galley:
        raise Http404

    content = journal_logic.get_galley_content(article, galley)

    template = 'journal/article.html'
    context = {
        'article': article,
        'galleys': galley,
        'article_content': content
    }

    return render(request, template, context)


from core.views import BaseUserList


class BCPAuthorSearch(BaseUserList):

    model = core_models.Account
    template_name = 'back_content/author_search.html'

    def dispatch(self, request, *args, **kwargs):
        self.article = get_object_or_404(
            models.Article,
            pk=kwargs.get('article_id'),
            journal=request.journal,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_facets(self):
        facets = {
            'q': {
                'type': 'search',
                'field_label': 'Search',
            },
            'is_active': {
                'type': 'boolean',
                'field_label': 'Active status',
                'true_label': 'Active',
                'false_label': 'Inactive',
            },
        }
        return self.filter_facets_if_journal(facets)

    def get_order_by_choices(self):
        return [
            ('last_name', _('Last name A-Z')),
            ('-last_name', _('Last name Z-A')),
            ('-date_joined', _('Newest')),
            ('date_joined', _('Oldest')),
        ]

    def get_queryset(self, params_querydict=None):
        return super().get_queryset().exclude(
            pk__in=self.article.authors.all().values_list(
                'pk',
                flat=True,
            ),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['article'] = self.article
        return context

    def post(self, request, *args, **kwargs):
        if "add_author" in request.POST:
            author_id = request.POST.get("add_author")
            author = get_object_or_404(
                core_models.Account,
                pk=author_id,
            )
            if author in self.get_queryset():
                author, created = models.FrozenAuthor.get_or_snapshot_if_email_found(
                    author.email,
                    self.article,
                )
                messages.success(
                    request,
                    f"{author} has been added as an author.",
                )

        # No role management, so call the grandparent's post.
        return super(BaseUserList, self).post(request, *args, **kwargs)