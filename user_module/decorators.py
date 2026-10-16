from functools import wraps

from django.shortcuts import redirect


def customer_required(view_func):
    """Allow only logged-in users whose role is CUSTOMER."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = request.user

        if not user.is_authenticated or user.role != 'CUSTOMER':
            return redirect('customer_login')

        return view_func(request, *args, **kwargs)

    return wrapper