# Public game id / URL slug: word_salad → salad.

from django.db import migrations

OLD_ID = 'word_salad'
NEW_ID = 'salad'


def _copy_game(Game, old, new_id):
    kwargs = {}
    for field in old._meta.fields:
        if field.primary_key:
            continue
        kwargs[field.attname] = getattr(old, field.attname)
    return Game.objects.create(id=new_id, **kwargs)


def _repoint_game_fks(apps, old_id, new_id):
    for model in apps.get_models(include_auto_created=True):
        for field in model._meta.fields:
            rel = getattr(field, 'related_model', None)
            if rel is None:
                continue
            meta = getattr(rel, '_meta', None)
            if meta is None:
                continue
            if meta.app_label == 'games' and meta.model_name == 'game':
                qs = model._default_manager
                qs.filter(**{field.attname: old_id}).update(**{field.attname: new_id})
        field_names = {field.name for field in model._meta.fields}
        if 'game_kind' in field_names:
            model._default_manager.filter(game_kind=old_id).update(game_kind=new_id)
        if 'game_instance_id' in field_names:
            prefix = old_id + ':'
            replacement = new_id + ':'
            for row in model._default_manager.filter(game_instance_id__startswith=prefix).iterator():
                row.game_instance_id = replacement + row.game_instance_id[len(prefix):]
                row.save(update_fields=['game_instance_id'])


def rename_word_salad_game(apps, schema_editor):
    Game = apps.get_model('games', 'Game')
    TaskGroup = apps.get_model('games', 'TaskGroup')
    old = Game.objects.filter(pk=OLD_ID).first()
    new = Game.objects.filter(pk=NEW_ID).first()
    if old is None and new is None:
        return
    if old is not None and new is None:
        new = _copy_game(Game, old, NEW_ID)
    if old is not None and new is not None and old.pk != new.pk:
        _repoint_game_fks(apps, OLD_ID, NEW_ID)
        Game.objects.filter(pk=OLD_ID).delete()
    for tg in TaskGroup.objects.filter(label__startswith=OLD_ID + ':'):
        tg.label = NEW_ID + tg.label[len(OLD_ID):]
        tg.save(update_fields=['label'])


def undo_rename_word_salad_game(apps, schema_editor):
    Game = apps.get_model('games', 'Game')
    TaskGroup = apps.get_model('games', 'TaskGroup')
    old = Game.objects.filter(pk=NEW_ID).first()
    previous = Game.objects.filter(pk=OLD_ID).first()
    if old is None:
        return
    if previous is None:
        _copy_game(Game, old, OLD_ID)
    _repoint_game_fks(apps, NEW_ID, OLD_ID)
    Game.objects.filter(pk=NEW_ID).delete()
    for tg in TaskGroup.objects.filter(label__startswith=NEW_ID + ':'):
        tg.label = OLD_ID + tg.label[len(NEW_ID):]
        tg.save(update_fields=['label'])


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0183_socialqueuepost_word_salad_source'),
    ]

    operations = [
        migrations.RunPython(rename_word_salad_game, undo_rename_word_salad_game),
    ]
