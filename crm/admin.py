from django.contrib import admin
# Financial and order records are managed only through audited application flows.
# Django's built-in user and group admin remains available for account management.
admin.site.site_header = "BIBAS · Управление доступом"
admin.site.site_title = "BIBAS CRM"
