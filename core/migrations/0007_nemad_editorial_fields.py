from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_newsarticle_editorial_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="newsarticle",
            name="industry_category",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="newsarticle",
            name="theme_axes",
            field=models.CharField(blank=True, default="", max_length=240),
        ),
        migrations.AddField(
            model_name="newsarticle",
            name="region",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="newsarticle",
            name="suitability_score",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="newsarticle",
            name="source_excerpt",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="newsarticle",
            name="rewritten_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="newsarticle",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("published", "Published"),
                    ("rejected", "Rejected"),
                    ("queued", "Queued"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
