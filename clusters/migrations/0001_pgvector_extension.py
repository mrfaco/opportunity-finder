"""Create the pgvector extension before any model migrations need it."""

from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        VectorExtension(),
    ]
