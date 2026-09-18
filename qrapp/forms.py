from django import forms
from .models import Student, College, Program, Major


class StudentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["college"].queryset = College.objects.filter(is_active=True).order_by("code")
        self.fields["college"].empty_label = "-- Select College --"

        college_id = None
        if self.is_bound:
            college_id = self.data.get("college")
        elif self.instance and self.instance.college_id:
            college_id = self.instance.college_id

        programs = Program.objects.filter(is_active=True).order_by("code")
        if college_id:
            programs = programs.filter(college_id=college_id)
        else:
            programs = programs.none()
        self.fields["program"].queryset = programs
        self.fields["program"].empty_label = "-- Select Program --"

        program_id = None
        if self.is_bound:
            program_id = self.data.get("program")
        elif self.instance and self.instance.program_id:
            program_id = self.instance.program_id

        majors = Major.objects.filter(is_active=True).order_by("code")
        if program_id:
            majors = majors.filter(program_id=program_id)
        else:
            majors = majors.none()
        self.fields["major"].queryset = majors
        self.fields["major"].empty_label = "-- Optional: Select Major --"

    class Meta:
        model = Student
        fields = ["student_id", "name", "sex", "college", "program", "major", "year"]
        widgets = {
            "student_id": forms.TextInput(attrs={"placeholder": "Enter student ID"}),
            "name": forms.TextInput(attrs={"placeholder": "Enter full name"}),
            "sex": forms.Select(
                choices=[
                    ("", "-- Select Gender --"),
                    ("M", "Male"),
                    ("F", "Female"),
                ]
            ),
            "college": forms.Select(attrs={"class": "form-control"}),
            "program": forms.Select(attrs={"class": "form-control"}),
            "major": forms.Select(attrs={"class": "form-control"}),
            "year": forms.NumberInput(attrs={"placeholder": "Enter year", "min": "1", "max": "5"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        program = cleaned_data.get("program")
        major = cleaned_data.get("major")
        if major and program and major.program_id != program.id:
            self.add_error(
                "major",
                f"{major} does not belong to the selected program.",
            )
        return cleaned_data


class StudentUploadForm(forms.Form):
    ALLOWED_EXTENSIONS = (".pdf", ".csv", ".xlsx", ".xlsm")
    MAX_SIZE_MB = 10

    student_file = forms.FileField(
        label="Student file",
        help_text="PDF, CSV, or Excel (.xlsx)",
    )

    def clean_student_file(self):
        upload = self.cleaned_data["student_file"]
        name = upload.name.lower()
        if not any(name.endswith(ext) for ext in self.ALLOWED_EXTENSIONS):
            raise forms.ValidationError("Upload a PDF, CSV, or Excel (.xlsx) file.")
        if upload.size > self.MAX_SIZE_MB * 1024 * 1024:
            raise forms.ValidationError(f"File must be smaller than {self.MAX_SIZE_MB}MB.")
        return upload


# Backward compatibility
PDFUploadForm = StudentUploadForm
