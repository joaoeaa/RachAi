"""Filtros e tags de apresentação do RachAi (dinheiro, iniciais, ícones)."""
from decimal import Decimal, InvalidOperation

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

# Traços dos ícones (viewBox 24x24, só stroke). Mantidos aqui para que cada
# template use o mesmo desenho sem repetir SVG.
ICONS = {
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "back": '<path d="m15 18-6-6 6-6"/>',
    "chevron": '<path d="m9 18 6-6-6-6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "user-plus": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M19 8v6M22 11h-6"/>',
    "receipt": '<path d="M4 2v20l3-2 2 2 3-2 3 2 2-2 3 2V2l-3 2-2-2-3 2-3-2-2 2Z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "edit": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    "trash": '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "check-circle": '<circle cx="12" cy="12" r="10"/><path d="m8 12 3 3 5-6"/>',
    "alert": '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
    "warn": '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "wallet": '<path d="M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0 0 4h14a2 2 0 0 1 2 2v3h-4a2 2 0 0 0 0 4h4v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    "link-off": '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/><path d="M3 3l18 18"/>',
    "copy": '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
    "message": '<path d="M21 11.5a8.4 8.4 0 0 1-12.3 7.4L3 21l2.1-5.7A8.4 8.4 0 1 1 21 11.5Z"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
    "key": '<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.7 12.3 9.8-9.8M17 6l3 3M14.5 8.5l2 2"/>',
    "mail": '<rect x="2" y="4" width="20" height="16" rx="3"/><path d="m22 7-10 6L2 7"/>',
    "lock": '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "owe": '<path d="M7 17 17 7M8 7h9v9"/>',
    "receive": '<path d="M17 7 7 17M16 17H7V8"/>',
    "minus": '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
    "equal": '<path d="M5 9h14M5 15h14"/>',
    "percent": '<path d="M19 5 5 19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>',
    "clipboard": '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M9 12h6M9 16h4"/>',
    "arrow-right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/>',
    "monitor": '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    # ícones de grupo
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "house": '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1Z"/>',
    "cup": '<path d="M6 3h12l-1.5 17a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1Z"/><path d="M6.5 8h11"/>',
    "cart": '<circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2 3h3l2.7 12.4a1 1 0 0 0 1 .8h9.6a1 1 0 0 0 1-.8L21 7H6"/>',
    "plane": '<path d="M10.2 13.8 3 11l1.5-1.5 9 1 4-4a2.1 2.1 0 0 1 3 3l-4 4 1 9L16 24l-2.8-7.2"/>',
    "flame": '<path d="M12 22a7 7 0 0 0 7-7c0-3-2-5.5-3.5-7-.4 2-1.5 3-2.5 3 .5-3-1-6-4-8 0 3-1.5 5-3 6.5A7 7 0 0 0 12 22Z"/>',
}

# Palavra no nome do grupo -> ícone. A primeira que bater vence.
GROUP_ICON_KEYWORDS = (
    (("praia", "verão", "verao", "sol"), "sun"),
    (("viagem", "férias", "ferias", "trip", "voo", "passeio"), "plane"),
    (("casa", "rep", "república", "republica", "apê", "ape", "ap.", "aluguel", "moradia", "condomínio"), "house"),
    (("churras",), "flame"),
    (("bar", "rolê", "role", "cerveja", "festa", "sexta", "happy", "aniversário", "aniversario"), "cup"),
    (("mercado", "compras", "feira"), "cart"),
)


def _to_decimal(value):
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _format_brl(amount):
    """Decimal -> '1.234,56' (sem sinal e sem prefixo)."""
    quantized = abs(_to_decimal(amount)).quantize(Decimal("0.01"))
    inteiro, _, centavos = f"{quantized:.2f}".partition(".")
    inteiro = f"{int(inteiro):,}".replace(",", ".")
    return f"{inteiro},{centavos}"


@register.filter
def brl(value):
    """Valor em reais, sempre positivo: ``R$ 1.234,56``."""
    return f"R$ {_format_brl(value)}"


@register.filter
def brl_signed(value):
    """Valor com sinal, para saldos: ``+R$ 10,00``, ``−R$ 10,00`` ou ``R$ 0,00``."""
    amount = _to_decimal(value)
    if abs(amount) < Decimal("0.005"):
        return "R$ 0,00"
    sign = "+" if amount > 0 else "−"
    return f"{sign}R$ {_format_brl(amount)}"


@register.filter
def decimal_input(value):
    """Valor para preencher um input: ``1234,56`` (sem milhar, com vírgula)."""
    return f"{abs(_to_decimal(value)).quantize(Decimal('0.01')):.2f}".replace(".", ",")


@register.filter
def initials(name):
    """Iniciais para o avatar: 'Ana Souza' -> 'AS', 'Rafa' -> 'RA'."""
    parts = [p for p in str(name or "").replace("@", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


@register.filter
def tone(value):
    """Uma das 4 cores de avatar/ícone, estável para o mesmo id."""
    try:
        return int(value) % 4 + 1
    except (TypeError, ValueError):
        return 1


@register.filter
def group_icon(name):
    """Ícone do grupo a partir de palavras no nome (praia, casa, bar...)."""
    lowered = str(name or "").lower()
    words = set(lowered.replace("·", " ").replace("-", " ").split())
    for keywords, icon_name in GROUP_ICON_KEYWORDS:
        for keyword in keywords:
            if keyword in words or (len(keyword) > 4 and keyword in lowered):
                return icon_name
    return "users"


@register.filter
def percent_of(value, total):
    """Porcentagem inteira de ``value`` sobre ``total`` (para barras)."""
    total = abs(_to_decimal(total))
    if not total:
        return 0
    return int(round(abs(_to_decimal(value)) / total * 100))


@register.simple_tag
def icon(name, size=20, extra_class=""):
    """SVG inline de um ícone de traço. Decorativo: sempre ``aria-hidden``."""
    paths = ICONS.get(name, ICONS["info"])
    cls = f"ic {extra_class}".strip()
    return format_html(
        '<svg class="{}" viewBox="0 0 24 24" width="{}" height="{}" aria-hidden="true" focusable="false">{}</svg>',
        cls,
        size,
        size,
        mark_safe(paths),
    )
