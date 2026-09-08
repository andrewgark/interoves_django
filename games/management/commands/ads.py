"""Manage Inter Oves Yandex Direct UNIFIED campaigns via API v501."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from games.ads_direct.client import DirectApiError, default_client
from games.ads_direct.config import ConfigError
from games.ads_direct import ops
from games.ads_direct.state import load_state, state_path


class Command(BaseCommand):
    help = "Yandex Direct v501 + Metrika ads manager (never prints OAuth tokens)."

    def add_arguments(self, parser):
        parser.add_argument("subcommand", nargs="?", default="status")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--no-moderate", action="store_true")
        parser.add_argument("--campaign-id", type=int)
        parser.add_argument("--adgroup-id", type=int)
        parser.add_argument("--id", dest="object_id", type=int)
        parser.add_argument("--date-from")
        parser.add_argument("--date-to")
        parser.add_argument("--wait", type=int, default=45, help="Seconds to wait when checking old-campaign spend")
        parser.add_argument("--ids", help="Comma-separated object IDs")
        parser.add_argument("--reason", default="")

    def handle(self, *args, **options):
        sub = options["subcommand"]
        dry = options["dry_run"]
        try:
            client = default_client()
            result = self._dispatch(client, sub, options, dry)
        except (DirectApiError, ConfigError) as exc:
            raise CommandError(str(exc)) from exc
        if options["json"] or not isinstance(result, str):
            self.stdout.write(ops.dump_json(result) if not isinstance(result, str) else result)

    def _dispatch(self, client, sub, options, dry):
        cid = options.get("campaign_id")
        if sub in ("status",):
            return ops.status(client)
        if sub == "preflight":
            return ops.preflight(client, spend_wait_seconds=options["wait"])
        if sub == "launch":
            return ops.launch(
                client,
                dry_run=dry,
                moderate=not options["no_moderate"],
                spend_wait_seconds=options["wait"],
            )
        if sub in ("client", "info", "client-info"):
            return ops.client_info(client)
        if sub == "dictionaries":
            return ops.dictionaries(client)
        if sub == "campaigns.get":
            ids = [cid] if cid else None
            return ops.campaigns_get(client, ids)
        if sub == "adgroups.get":
            return ops.adgroups_get(client, campaign_ids=[cid] if cid else None, ids=_ids(options))
        if sub == "ads.get":
            return ops.ads_get(client, campaign_ids=[cid] if cid else None, ids=_ids(options))
        if sub == "keywords.get":
            return ops.keywords_get(client, campaign_ids=[cid] if cid else None, ids=_ids(options))
        if sub == "bidmodifiers.get":
            return ops.bidmodifiers_get(client, campaign_ids=[cid] if cid else None, ids=_ids(options))
        if sub == "campaigns.suspend":
            return ops.campaign_suspend(client, cid, dry_run=dry, reason=options["reason"])
        if sub == "campaigns.resume":
            return ops.campaign_resume(client, cid, dry_run=dry, reason=options["reason"])
        if sub == "keywords.suspend":
            return ops.keywords_suspend(client, _id_list(options), dry_run=dry, reason=options["reason"])
        if sub == "keywords.resume":
            return ops.keywords_resume(client, _id_list(options), dry_run=dry, reason=options["reason"])
        if sub == "metrika-goals":
            return ops.metrika_goals(client)
        if sub == "reports":
            target = cid or load_state().get("current_managed_campaign_id")
            if not target:
                raise ConfigError("Need --campaign-id")
            date_from = options["date_from"] or load_state().get("campaign_creation_date")
            date_to = options["date_to"]
            if not date_from or not date_to:
                raise ConfigError("Need --date-from and --date-to")
            return ops.campaign_report(client, int(target), date_from=date_from, date_to=date_to)
        if sub == "state":
            return {"path": str(state_path()), "state": load_state()}
        raise CommandError(
            "Unknown subcommand. Use: status, preflight, launch, client, dictionaries, "
            "campaigns.get, adgroups.get, ads.get, keywords.get, bidmodifiers.get, "
            "campaigns.suspend, campaigns.resume, keywords.suspend, keywords.resume, "
            "metrika-goals, reports, state"
        )


def _ids(options):
    if options.get("object_id"):
        return [options["object_id"]]
    return None


def _id_list(options):
    if options.get("ids"):
        return [int(x) for x in str(options["ids"]).split(",") if x.strip()]
    if options.get("object_id"):
        return [options["object_id"]]
    raise ConfigError("Need --ids")
