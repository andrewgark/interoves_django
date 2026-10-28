"""Puzzle pages that task texts link to."""

from django.http import HttpResponse
from django.shortcuts import render


# for game 29 :)
def return_intentional_503(request):
    return HttpResponse(status=503)


# for game 54
def easter_egg_2021(request):
    return render(request, 'site/easter_egg_2021.html')
