"""Website login guard. request.member is set by SiteMemberMiddleware (None if logged out/archived)."""
from functools import wraps

from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode


def login_required_member(view):
    """Redirect to the `login` page (with ?next=) unless request.member is set."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.member is None:
            return redirect(f"{reverse('login')}?{urlencode({'next': request.get_full_path()})}")
        return view(request, *args, **kwargs)
    return wrapper
