from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='complaint',
            name='original_language',
            field=models.CharField(choices=[('en', 'English'), ('kn', 'Kannada')], default='en', max_length=5),
        ),
        migrations.AddField(
            model_name='complaint',
            name='input_method',
            field=models.CharField(choices=[('TYPED', 'Typed'), ('VOICE', 'Voice (speech-to-text)'), ('ASSISTANT', 'AI assistant')], default='TYPED', max_length=10),
        ),
    ]
