from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("add/", views.add_random, name="add_random"),
    path("clear/", views.clear_all, name="clear_all"),
]
