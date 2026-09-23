# from django.apps import AppConfig

# class AcademicConfig(AppConfig):
#     default_auto_field = 'django.db.models.BigAutoField'
#     name = 'academic'

### New code for PyMongo

# academic/apps.py
from django.apps import AppConfig

class AcademicConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'academic'
    verbose_name = "Academic"

    def ready(self):
        pass
