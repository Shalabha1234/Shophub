from functools import wraps
from django.shortcuts import redirect


def seller_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated or user.role != 'SELLER':
            return redirect('seller_login')
        return view_func(request, *args, **kwargs)
    return wrapper