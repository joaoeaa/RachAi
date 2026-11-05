from django.utils.functional import SimpleLazyObject


def navigation(request):
    """
    Grupos do usuário para a navegação (barra lateral e chips do celular).

    É preguiçoso: a consulta só roda se o template realmente usar a lista, então
    páginas sem navegação (login, convite) não pagam nada por isso.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    def load():
        from .views import _sidebar_groups_qs
        return list(_sidebar_groups_qs(user))

    return {"nav_groups": SimpleLazyObject(load)}
