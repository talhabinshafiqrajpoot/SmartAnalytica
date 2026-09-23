"""Forms for SmartAnalytica."""

from __future__ import annotations

import secrets

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .detection.pipeline import METHOD_CHOICES
from .models import Assignment, Course, Submission, UserProfile
from .services import validate_upload

BOOTSTRAP = {'class': 'form-control'}


class RegistrationForm(UserCreationForm):
    full_name = forms.CharField(max_length=100, required=True)
    email = forms.EmailField(max_length=200, required=True)
    phone_number = forms.CharField(max_length=15, required=True)
    ROLE_CHOICES = [('student', 'Student'), ('teacher', 'Teacher')]
    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        widget=forms.RadioSelect,
        initial='student',
        help_text='Pick the role this account should have.',
    )
    teacher_access_code = forms.CharField(
        required=False,
        widget=forms.PasswordInput(
            render_value=True,
            attrs={'class': 'form-control', 'autocomplete': 'off'},
        ),
        label='Teacher access code',
        help_text='Issued by the department. Only needed to register as a teacher.',
    )

    class Meta:
        model = User
        fields = ('username', 'full_name', 'email', 'phone_number', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name != 'role':
                field.widget.attrs.update(BOOTSTRAP)

    def clean(self):
        """
        Gate the teacher role behind the departmental access code.

        Validated on the server, so hiding or re-enabling the field in the
        browser achieves nothing. The comparison is constant-time to avoid
        leaking the code one character at a time through response timing.
        """
        cleaned = super().clean()
        if cleaned.get('role') != 'teacher':
            # Never carry a stray code through on a student registration.
            cleaned['teacher_access_code'] = ''
            return cleaned

        expected = (getattr(settings, 'TEACHER_ACCESS_CODE', '') or '').strip()
        if not expected:
            self.add_error(
                'role',
                'Teacher registration is currently disabled. '
                'Ask an administrator to create your account.',
            )
            return cleaned

        supplied = (cleaned.get('teacher_access_code') or '').strip()
        if not supplied:
            self.add_error('teacher_access_code',
                           'A teacher access code is required to register as a teacher.')
        elif not secrets.compare_digest(supplied, expected):
            # Deliberately the same wording as a missing code, so the form
            # never confirms that a guessed code was "close".
            self.add_error('teacher_access_code',
                           'That teacher access code is not valid. '
                           'Contact the department office.')
        return cleaned

    def clean_email(self):
        email = self.cleaned_data['email']
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
            role = self.cleaned_data['role']
            # get_or_create, because a superuser created via createsuperuser
            # then registering would otherwise hit the OneToOne constraint.
            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    'is_student': role == 'student',
                    'is_teacher': role == 'teacher',
                    'full_name': self.cleaned_data['full_name'],
                    'email': self.cleaned_data['email'],
                    'phone_number': self.cleaned_data['phone_number'],
                },
            )
        return user


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = [
            'name', 'course_code', 'description', 'start_date', 'end_date',
            'instructors', 'department', 'credits', 'prerequisites',
        ]
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date', **BOOTSTRAP}),
            'end_date': forms.DateInput(attrs={'type': 'date', **BOOTSTRAP}),
            'description': forms.Textarea(attrs={'rows': 3, **BOOTSTRAP}),
            'prerequisites': forms.Textarea(attrs={'rows': 2, **BOOTSTRAP}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start_date'), cleaned.get('end_date')
        if start and end and end < start:
            self.add_error('end_date', 'The end date cannot be before the start date.')
        return cleaned


class AssignmentForm(forms.ModelForm):
    class Meta:
        model = Assignment
        fields = [
            'course', 'title', 'description', 'due_date', 'submission_format',
            'max_marks', 'file_upload_instructions', 'attachments', 'grading_criteria',
        ]
        widgets = {
            'due_date': forms.DateTimeInput(attrs={'type': 'datetime-local', **BOOTSTRAP}),
            'description': forms.Textarea(attrs={'rows': 3, **BOOTSTRAP}),
            'file_upload_instructions': forms.Textarea(attrs={'rows': 2, **BOOTSTRAP}),
            'grading_criteria': forms.Textarea(attrs={'rows': 2, **BOOTSTRAP}),
        }

    def __init__(self, *args, teacher=None, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')
        # A teacher must not be able to attach an assignment to someone else's
        # course by editing the select's value.
        if teacher is not None:
            self.fields['course'].queryset = Course.objects.filter(teacher=teacher)

    def clean_max_marks(self):
        marks = self.cleaned_data['max_marks']
        if marks <= 0:
            raise forms.ValidationError('Maximum marks must be greater than zero.')
        return marks


class EnrollStudentsForm(forms.Form):
    course = forms.ModelChoiceField(queryset=Course.objects.none(), widget=forms.Select(attrs=BOOTSTRAP))
    students = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': 10}),
        help_text='Hold Cmd (Mac) or Ctrl (Windows) to select several.',
    )

    def __init__(self, *args, teacher=None, **kwargs):
        super().__init__(*args, **kwargs)
        courses = Course.objects.all() if teacher is None else Course.objects.filter(teacher=teacher)
        self.fields['course'].queryset = courses
        self.fields['students'].queryset = User.objects.filter(
            userprofile__is_student=True
        ).order_by('username')


class SubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission
        fields = ['submitted_file']
        widgets = {'submitted_file': forms.ClearableFileInput(attrs={'class': 'form-control'})}

    def clean_submitted_file(self):
        uploaded = self.cleaned_data['submitted_file']
        errors = validate_upload(uploaded)
        if errors:
            raise forms.ValidationError(errors)
        return uploaded


class DetectionRunForm(forms.Form):
    """The 'select plagiarism detection algorithm' control from test case TC7."""

    method = forms.ChoiceField(
        choices=METHOD_CHOICES,
        initial='combined',
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Detection algorithm',
    )
    threshold = forms.FloatField(
        initial=40.0,
        min_value=1.0,
        max_value=100.0,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '1'}),
        label='Flagging threshold (%)',
        help_text='Submissions at or above this similarity are flagged for review.',
    )
