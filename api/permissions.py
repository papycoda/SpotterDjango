from rest_framework.permissions import BasePermission


class IsOperationsAdmin(BasePermission):
    """Allow operational mutations only for authenticated staff users."""

    message = 'Staff access is required for operations endpoints.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_staff)
