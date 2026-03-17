from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("add/", views.add_random, name="add_random"),
    path("clear/", views.clear_all, name="clear_all"),
    path("records/", views.records, name="records"),
    path("records/add/", views.add_random_record, name="add_random_record"),
    path("records/clear/", views.clear_all_records, name="clear_all_records"),
]
