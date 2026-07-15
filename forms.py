from django import forms
from django.utils.translation import gettext_lazy as _

from submission import models
from review.logic import render_choices
from utils.forms import KeywordModelForm
from core import models as core_models, model_utils


class PublicationInfo(forms.ModelForm):
    class Meta:
        model = models.Article
        fields = (
            'date_accepted',
            'date_published',
            'page_numbers',
            'primary_issue',
            'peer_reviewed',
            'render_galley',
            'stage',
        )
        help_texts = {
            'render_galley': 'Render Galleys are displayed on the Article page'
                             ' generally this will be an XML or HTML Galley.',
            'page_numbers': 'Tis is a free text field, generally page numbers'
                            ' look like 10 or 10-16',
            'peer_reviewed': 'If this article has been peer reviewed prior to'
                             'being loaded into Janeway check this box.'
        }
        widgets = {
            'date_accepted': model_utils.DateTimePickerInput,
        }

    def __init__(self, *args, **kwargs):
        super(PublicationInfo, self).__init__(*args, **kwargs)
        if 'instance' in kwargs:
            article = kwargs['instance']
            self.fields['primary_issue'].queryset = article.journal.issue_set.all()
            self.fields['render_galley'].queryset = article.galley_set.all()


class RemoteArticle(forms.ModelForm):
    class Meta:
        model = models.Article
        fields = ('is_remote', 'remote_url')


class RemoteParse(forms.Form):
    url = forms.CharField(required=True, label="Enter a URL or a DOI.")
    mode = forms.ChoiceField(required=True, choices=(('url', 'URL'), ('doi', 'DOI')))


class ArticleInfo(KeywordModelForm):

    class Meta:
        model = models.Article
        fields = (
        'title', 'subtitle', 'abstract', 'non_specialist_summary',
        'language', 'section', 'license', 'primary_issue',
        'page_numbers', 'is_remote', 'remote_url', 'peer_reviewed')
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': _('Title')}),
        }

    def __init__(self, *args, **kwargs):
        elements = kwargs.pop('additional_fields', None)
        submission_summary = kwargs.pop('submission_summary', None)
        journal = kwargs.pop('journal', None)

        super(ArticleInfo, self).__init__(*args, **kwargs)
        if 'instance' in kwargs:
            article = kwargs['instance']
            self.fields['section'].queryset = models.Section.objects.filter(
                journal=article.journal,
            )
            self.fields['license'].queryset = models.Licence.objects.filter(
                journal=article.journal,
                available_for_submission=True,
            )
            self.fields['section'].required = True
            self.fields['license'].required = True
            self.fields['primary_issue'].queryset = article.journal.issues

            abstracts_required = article.journal.get_setting(
                'general',
                'abstract_required',
            )

            if abstracts_required:
                self.fields['abstract'].required = True

            if submission_summary:
                self.fields['non_specialist_summary'].required = True

            # Pop fields based on journal.submissionconfiguration
            if journal:
                if not journal.submissionconfiguration.subtitle:
                    self.fields.pop('subtitle')

                if not journal.submissionconfiguration.abstract:
                    self.fields.pop('abstract')

                if not journal.submissionconfiguration.language:
                    self.fields.pop('language')

                if not journal.submissionconfiguration.license:
                    self.fields.pop('license')

                if not journal.submissionconfiguration.keywords:
                    self.fields.pop('keywords')

                if not journal.submissionconfiguration.section:
                    self.fields.pop('section')

            # Add additional fields
            if elements:
                for element in elements:
                    if element.kind == 'text':
                        self.fields[element.name] = forms.CharField(
                            widget=forms.TextInput(attrs={'div_class': element.width}),
                            required=element.required)
                    elif element.kind == 'textarea':
                        self.fields[element.name] = forms.CharField(widget=forms.Textarea,
                                                                    required=element.required)
                    elif element.kind == 'date':
                        self.fields[element.name] = forms.CharField(
                            widget=forms.DateInput(attrs={'class': 'datepicker', 'div_class': element.width}),
                            required=element.required)

                    elif element.kind == 'select':
                        choices = render_choices(element.choices)
                        self.fields[element.name] = forms.ChoiceField(
                            widget=forms.Select(attrs={'div_class': element.width}), choices=choices,
                            required=element.required)

                    elif element.kind == 'email':
                        self.fields[element.name] = forms.EmailField(
                            widget=forms.TextInput(attrs={'div_class': element.width}),
                            required=element.required)
                    elif element.kind == 'check':
                        self.fields[element.name] = forms.BooleanField(
                            widget=forms.CheckboxInput(attrs={'is_checkbox': True}),
                            required=element.required)

                    self.fields[element.name].help_text = element.help_text
                    self.fields[element.name].label = element.name

                    if article:
                        try:
                            check_for_answer = models.FieldAnswer.objects.get(field=element, article=article)
                            self.fields[element.name].initial = check_for_answer.answer
                        except models.FieldAnswer.DoesNotExist:
                            pass

    def save(self, commit=True, request=None):
        article = super(ArticleInfo, self).save(commit=False)

        if request:
            additional_fields = models.Field.objects.filter(journal=request.journal)

            for field in additional_fields:
                answer = request.POST.get(field.name, None)
                if answer:
                    try:
                        field_answer = models.FieldAnswer.objects.get(article=article, field=field)
                        field_answer.answer = answer
                        field_answer.save()
                    except models.FieldAnswer.DoesNotExist:
                        field_answer = models.FieldAnswer.objects.create(article=article, field=field, answer=answer)

            request.journal.submissionconfiguration.handle_defaults(article)

        if commit:
            article.save()

        return article


class ExistingAuthor(forms.Form):
    author = forms.ModelChoiceField(
        queryset=core_models.Account.objects.all()
    )
