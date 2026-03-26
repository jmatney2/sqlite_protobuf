from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("add/", views.add_random, name="add_random"),
    path("clear/", views.clear_all, name="clear_all"),
    path("records/", views.records, name="records"),
    path("records/export.csv", views.records_csv, name="records_csv"),
    path("records/columns/", views.save_record_columns, name="save_record_columns"),
    path("records/configs/save/", views.save_named_config, name="save_named_config"),
    path("records/configs/delete/", views.delete_named_config, name="delete_named_config"),
    path("records/add/", views.add_random_record, name="add_random_record"),
    path("records/clear/", views.clear_all_records, name="clear_all_records"),
]
