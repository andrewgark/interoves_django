from django.db import migrations, models


def populate_replacements_answer_rows(apps, schema_editor):
    Task = apps.get_model('games', 'Task')
    from games.replacements_lines import (
        parse_replacements_checker_json_lines,
        parse_replacements_lines_text,
    )

    queryset = Task.objects.filter(task_type='replacements_lines').only(
        'pk', 'text', 'checker_data',
    ).iterator()
    for task in queryset:
        raw = (task.checker_data or '').strip()
        parsed = parse_replacements_checker_json_lines(raw) if raw else None
        if parsed:
            rows, _ = parsed
            count = len(rows)
        else:
            parsed_text = parse_replacements_lines_text(task.text or '', raw or None)
            count = len(parsed_text.get('left_lines') or [])
        Task.objects.filter(pk=task.pk).update(replacements_answer_rows=count)


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0261_daily_result_projection_attempts'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='replacements_answer_rows',
            field=models.PositiveIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(populate_replacements_answer_rows, migrations.RunPython.noop),
    ]
