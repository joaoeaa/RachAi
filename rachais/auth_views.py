from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from .models import Profile, clean_pix_key

User = get_user_model()

# Convidados não têm senha: a sessão é o que os mantém dentro, então dura bem mais
# que o padrão de duas semanas do Django.
GUEST_SESSION_AGE = 60 * 60 * 24 * 365


def _safe_next(request):
    """O ``next`` da requisição, se apontar para este próprio site (evita open redirect)."""
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return None


def _auth_context(request, **extra):
    """Contexto das telas de login/cadastro: avisa quando a pessoa veio de um convite."""
    next_url = _safe_next(request) or ""
    return {"invite_pending": next_url.startswith("/convite/"), **extra}


def _credentials_error(email, password, confirm, exclude_user=None):
    """Primeiro problema com e-mail/senha de um cadastro, ou ``None``."""
    if not email or "@" not in email:
        return "Informe um e-mail válido."
    taken = User.objects.filter(username__iexact=email) | User.objects.filter(email__iexact=email)
    if exclude_user is not None:
        taken = taken.exclude(pk=exclude_user.pk)
    if taken.exists():
        return "Já existe uma conta com esse e-mail."
    if not password or not confirm:
        return "Informe e confirme a senha."
    if password != confirm:
        return "As senhas não conferem."
    if len(password) < 8:
        return "A senha deve ter pelo menos 8 caracteres."
    return None


def login_guest(request, user):
    """Loga um convidado (sem senha) e marca a sessão para lembrar dele por mais tempo."""
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session.set_expiry(GUEST_SESSION_AGE)
    request.session["is_guest"] = True


def welcome_view(request):
    return render(request, "rachais/index.html")


@require_http_methods(["GET", "POST"])
def login_form_view(request):
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        password = (request.POST.get("password") or "")

        user = authenticate(request, username=email, password=password)

        if user is not None:
            login(request, user)
            return redirect(_safe_next(request) or "rachais:home")

        messages.error(request, "E-mail ou senha inválidos.")

    return render(request, "rachais/login.html", _auth_context(request))


def logout_view(request):
    logout(request)
    return redirect("accounts:welcome")


@require_http_methods(["GET", "POST"])
def register(request):
    form = {}
    if request.method == "POST":
        form = {
            "first_name": (request.POST.get("first_name") or "").strip(),
            "email": (request.POST.get("email") or "").strip().lower(),
            "pix_key": request.POST.get("pix_key") or "",
        }
        password = (request.POST.get("password") or "")
        confirm = (request.POST.get("confirm_password") or "")
        pix_key, pix_error = clean_pix_key(form["pix_key"])

        if not form["first_name"]:
            error = "Informe seu primeiro nome."
        else:
            error = _credentials_error(form["email"], password, confirm) or pix_error

        if error:
            messages.error(request, error)
        else:
            user = User.objects.create_user(
                username=form["email"],
                email=form["email"],
                password=password,
                first_name=form["first_name"],
            )
            Profile.objects.create(user=user, pix_key=pix_key)
            user = authenticate(request, username=form["email"], password=password)
            if user is not None:
                login(request, user)
                return redirect(_safe_next(request) or "rachais:home")
    return render(request, "rachais/signup.html", _auth_context(request, form=form))


@login_required
@require_http_methods(["GET", "POST"])
def account(request):
    """
    Minha conta: nome e chave Pix. Para convidados, também mostra o link pessoal de
    acesso e permite completar o cadastro com e-mail e senha.
    """
    user = request.user
    profile = Profile.for_user(user)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "profile":
            first_name = (request.POST.get("first_name") or "").strip()
            pix_key, pix_error = clean_pix_key(request.POST.get("pix_key"))
            if not first_name:
                messages.error(request, "Informe seu nome.")
            elif len(first_name) > 60:
                messages.error(request, "O nome deve ter no máximo 60 caracteres.")
            elif pix_error:
                messages.error(request, pix_error)
            else:
                user.first_name = first_name
                user.save(update_fields=["first_name"])
                profile.pix_key = pix_key
                profile.save(update_fields=["pix_key"])
                messages.success(request, "Dados atualizados.")
                return redirect("accounts:account")

        elif action == "finish" and profile.is_guest:
            email = (request.POST.get("email") or "").strip().lower()
            password = request.POST.get("password") or ""
            confirm = request.POST.get("confirm_password") or ""
            error = _credentials_error(email, password, confirm, exclude_user=user)
            if error:
                messages.error(request, error)
            else:
                user.username = email
                user.email = email
                user.set_password(password)
                user.save()
                profile.is_guest = False
                profile.access_token = None
                profile.save(update_fields=["is_guest", "access_token"])
                update_session_auth_hash(request, user)
                request.session.pop("is_guest", None)
                messages.success(request, "Cadastro completo! Agora você entra com seu e-mail e senha.")
                return redirect("accounts:account")

    access_url = None
    if profile.is_guest and profile.access_token:
        access_url = request.build_absolute_uri(
            reverse("accounts:guest_access", args=[profile.access_token])
        )

    return render(
        request,
        "rachais/account.html",
        {"profile": profile, "access_url": access_url},
    )


@require_http_methods(["GET", "POST"])
def guest_access(request, token):
    """
    Link pessoal de um convidado, para voltar de outro aparelho ou navegador.

    O GET só pergunta; o login acontece no POST, para que ninguém seja logado na
    conta de outra pessoa só por abrir um link.
    """
    profile = (
        Profile.objects.select_related("user")
        .filter(access_token=token, is_guest=True)
        .first()
    )

    if profile is None:
        messages.error(request, "Esse link de acesso não vale mais. Entre com seu e-mail e senha.")
        return redirect("accounts:login")

    if request.user.is_authenticated and request.user.pk == profile.user_id:
        return redirect("rachais:home")

    if request.method == "POST":
        login_guest(request, profile.user)
        return redirect("rachais:home")

    return render(request, "rachais/guest_access.html", {"guest": profile.user})
