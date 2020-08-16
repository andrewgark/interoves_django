from allauth.account.adapter import DefaultAccountAdapter
from games.views import redirect_to_referer


class AccountAdapter(DefaultAccountAdapter):
    def get_login_redirect_url(self, request):
        if 'HTTP_REFERER' not in request.META:
            return '/'
        return request.META.get('HTTP_REFERER')
