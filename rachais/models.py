import secrets

from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower  # <-- novo
from django.utils import timezone


def user_display_name(user):
    """Melhor nome legível disponível para um usuário cadastrado."""
    if not user:
        return "Usuário"

    full = (user.get_full_name() or "").strip()
    if full:
        return full

    first = (getattr(user, "first_name", "") or "").strip()
    if first:
        return first

    uname = (getattr(user, "username", "") or "").strip()
    if "@" in uname:
        uname = uname.split("@", 1)[0]
    return uname or "Usuário"


class Group(models.Model):
    MODE_SHARED = "SHARED"
    MODE_MANAGED = "MANAGED"
    MODE_CHOICES = [
        (MODE_SHARED, "Colaborativo — cada participante entra com sua conta"),
        (MODE_MANAGED, "Gerenciado por mim — eu cadastro os nomes e os gastos"),
    ]

    name = models.CharField(max_length=100, verbose_name="Nome do Grupo")  # <- sem unique=True
    creator = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_groups')
    mode = models.CharField(
        max_length=10,
        choices=MODE_CHOICES,
        default=MODE_SHARED,
        verbose_name="Tipo do grupo",
    )
    # Código do link de convite (grupos colaborativos). Gerado sob demanda; trocar o
    # código invalida o link antigo.
    invite_token = models.CharField(
        max_length=32,
        unique=True,
        null=True,
        blank=True,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("name"), "creator",
                name="uniq_group_name_per_creator_ci"
            )
        ]

    @property
    def is_managed(self):
        """True quando o grupo é tocado por uma pessoa só, com participantes avulsos."""
        return self.mode == self.MODE_MANAGED

    def ensure_invite_token(self):
        """Devolve o código do convite, criando um na primeira vez."""
        if not self.invite_token:
            self.regenerate_invite_token()
        return self.invite_token

    def regenerate_invite_token(self):
        """Troca o código do convite; quem tiver o link antigo não entra mais."""
        self.invite_token = secrets.token_urlsafe(12)
        self.save(update_fields=["invite_token"])
        return self.invite_token

    def __str__(self):
        return self.name


class Participant(models.Model):
    """
    Alguém que participa da divisão de um grupo.

    Pode ser um usuário cadastrado (``user``) ou um participante avulso, que existe
    apenas como um nome dentro do grupo (``guest_name``) e não precisa de conta.
    """
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='participants')
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='group_participations',
        null=True,
        blank=True,
    )
    guest_name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Nome do participante avulso",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["group", "user"],
                condition=Q(user__isnull=False),
                name="uniq_participant_user_per_group",
            ),
            models.UniqueConstraint(
                Lower("guest_name"), "group",
                condition=~Q(guest_name=""),
                name="uniq_participant_guest_name_per_group_ci",
            ),
            models.CheckConstraint(
                condition=Q(user__isnull=False) | ~Q(guest_name=""),
                name="participant_has_user_or_guest_name",
            ),
        ]

    @property
    def is_guest(self):
        return self.user_id is None

    @property
    def display_name(self):
        if self.user_id:
            return user_display_name(self.user)
        return (self.guest_name or "").strip() or "Participante"

    @property
    def pix_key(self):
        """Chave Pix de quem tem conta; participantes avulsos não têm."""
        if not self.user_id:
            return ""
        try:
            return self.user.profile.pix_key
        except ObjectDoesNotExist:
            return ""

    def __str__(self):
        return f"{self.display_name} ({self.group.name})"


class Expense(models.Model):
    """Representa uma despesa registrada em um grupo."""
    SPLIT_METHOD_CHOICES= [
        ('EQUAL','Dividir igualmente'),
        ('UNEQUAL_VALUE','Dividir por valores exatos'),
        ('UNEQUAL_PERCENTAGE','Dividir por porcentagem'),
    ]
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='expenses')
    description = models.CharField(max_length=255, verbose_name="Descrição da Despesa")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Valor")
    paid_by = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name='paid_expenses', verbose_name="Quem pagou")
    created_at = models.DateTimeField(auto_now_add=True)

    split_method=models.CharField(
        max_length=20,
        choices=SPLIT_METHOD_CHOICES,
        default='EQUAL'
    )
    participants=models.ManyToManyField(
        Participant,
        through='ExpenseSplit',
        related_name='expenses_participated'
    )
    def __str__(self):
        return f"{self.description} (R$ {self.amount}) em {self.group.name}"

    class Meta:
        verbose_name = "Despesa"
        verbose_name_plural = "Despesas"
        ordering = ['-created_at']
class ExpenseSplit(models.Model):
    expense=models.ForeignKey(Expense,on_delete=models.CASCADE,related_name='splits')
    participant=models.ForeignKey(Participant,on_delete=models.CASCADE,related_name='splits')
    amount_owed=models.DecimalField(max_digits=10, decimal_places=2,verbose_name="valor devido ")

    class Meta:
        unique_together=('expense','participant')
    def __str__(self):
        return f"{self.participant.display_name} deve R$ {self.amount_owed} para {self.expense.description}"

class Payment(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="payments")
    payer = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="payments_made")
    receiver = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="payments_received")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payments_registered")
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.payer} pagou R$ {self.amount} para {self.receiver} em {self.group}"


PIX_KEY_MAX_LENGTH = 77  # maior chave Pix possível (e-mail)


def clean_pix_key(raw):
    """Normaliza a chave Pix digitada. Devolve ``(chave, erro)``."""
    key = " ".join((raw or "").split())
    if len(key) > PIX_KEY_MAX_LENGTH:
        return None, f"A chave Pix deve ter no máximo {PIX_KEY_MAX_LENGTH} caracteres."
    return key, None


class Profile(models.Model):
    """
    Dados extras de quem usa o RachAi.

    Convidados (``is_guest``) entraram num grupo só com um nome: têm conta, mas sem
    e-mail nem senha. Voltam pelo link pessoal (``access_token``) até completarem
    o cadastro.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    pix_key = models.CharField(max_length=PIX_KEY_MAX_LENGTH, blank=True, verbose_name="Chave Pix")
    is_guest = models.BooleanField(default=False, verbose_name="Convidado sem cadastro")
    access_token = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        editable=False,
    )

    @classmethod
    def for_user(cls, user):
        try:
            return user.profile
        except ObjectDoesNotExist:
            profile, _ = cls.objects.get_or_create(user=user)
            return profile

    def __str__(self):
        return f"Perfil de {user_display_name(self.user)}"


def create_guest_user(name):
    """Cria a conta de um convidado que entrou num grupo só com o nome."""
    user = User(username=f"convidado-{secrets.token_hex(8)}", first_name=name)
    user.set_unusable_password()
    user.save()
    Profile.objects.create(user=user, is_guest=True, access_token=secrets.token_urlsafe(32))
    return user
