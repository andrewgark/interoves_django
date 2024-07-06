from django.http import HttpResponseRedirect, HttpResponse


def redirect_to_referer(request):
    if 'HTTP_REFERER' in request.META:
        return HttpResponseRedirect(request.META.get('HTTP_REFERER'))
    if 'next' in request.POST:
        return HttpResponseRedirect(request.POST.get('next'))
    return 


def has_profile(user):
    return user and getattr(user, 'profile', None)


def has_team(user):
    return has_profile(user) and user.profile.team_on
