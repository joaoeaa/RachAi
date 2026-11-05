from django.contrib import admin

from rachais.models import Expense, Group, Participant, Profile

# Register your models here.
admin.site.register(Group)
admin.site.register(Participant)
admin.site.register(Expense)
admin.site.register(Profile)
