# ZenTao and GitLab Issue Sync

This repository includes a polling sync tool for environments where GitLab cannot call back to the ZenTao server. The script runs next to ZenTao, pulls GitLab issues through the GitLab API, and writes changes into the ZenTao database.

## Scope

The sync tool maps one GitLab issue to one ZenTao object:

| GitLab labels | ZenTao object |
| --- | --- |
| `bug`, `缺陷` | Bug |
| `feature`, `enhancement`, `story`, `需求`, `feature:*` | Story |
| No type label | Uses `default_type`, currently `bug` |

Progress is represented by GitLab labels:

| GitLab progress label | ZenTao bug status | ZenTao story status/stage |
| --- | --- | --- |
| `status:wait` | `active` | `active` / `wait` |
| `status:doing` | `active` | `active` / `developing` |
| `status:done` | `closed` | `active` / `developed` |
| `status:closed` | `closed` | `closed` / `closed` |

The tool preserves existing business labels such as `feature:simple-list`, `pcap`, or `test`. It only replaces managed type/progress labels when syncing back to GitLab.

## Files

- `tools/zentao_gitlab_issue_sync.php` - sync script.
- `tools/zentao_gitlab_issue_sync.config.example.php` - configuration template. Copy it to `zentao_gitlab_issue_sync.config.php` on the ZenTao server and fill secrets there.
- `docs/zentao-gitlab-sync.md` - this deployment and operations guide.

Do not commit the real config file because it contains the GitLab token and database password.

## Deployment

The recommended deployment location for the ZenTao Docker package is a persistent host directory mounted into the container:

```bash
ssh root@10.11.106.80 'mkdir -p /data/zentao/sync && chmod 700 /data/zentao/sync'
scp tools/zentao_gitlab_issue_sync.php root@10.11.106.80:/data/zentao/sync/
scp tools/zentao_gitlab_issue_sync.config.example.php root@10.11.106.80:/data/zentao/sync/zentao_gitlab_issue_sync.config.php
ssh root@10.11.106.80 'chmod 755 /data/zentao/sync/zentao_gitlab_issue_sync.php && chmod 600 /data/zentao/sync/zentao_gitlab_issue_sync.config.php'
```

Edit `/data/zentao/sync/zentao_gitlab_issue_sync.config.php` on the server:

- Set `gitlab_url`.
- Set `gitlab_token`.
- Set `gitlab_id`, `gitlab_project_id`.
- Set `zentao_product_id`, `zentao_project_id`, and `zentao_execution_id`.
- Adjust label arrays if the project uses different label names.

Inside the ZenTao container, the script path is usually `/data/sync/zentao_gitlab_issue_sync.php`.

## First Run

Run the script manually before adding cron:

```bash
ssh root@10.11.106.80 'docker exec zentao /opt/zbox/bin/php /data/sync/zentao_gitlab_issue_sync.php'
```

Expected behavior:

- Existing GitLab issues are imported into ZenTao.
- Existing type labels decide whether each issue becomes a ZenTao bug or story.
- Missing progress labels are written back to GitLab.
- A second run may update ZenTao timestamps after GitLab labels were written.
- A third run should normally print only `sync started` and `sync finished`.

Useful database checks:

```sql
SELECT zentao_type, COUNT(*) FROM zt_gitlab_issue_sync GROUP BY zentao_type;
SELECT COUNT(*) FROM zt_bug WHERE product = 1 AND execution = 2 AND deleted = 0;
SELECT COUNT(*) FROM zt_story WHERE product = 1 AND deleted = 0;
```

## Cron

Use host cron to run the script every five minutes:

```bash
(crontab -l 2>/dev/null | grep -v 'zentao_gitlab_issue_sync.php'; \
  echo '*/5 * * * * docker exec zentao /opt/zbox/bin/php /data/sync/zentao_gitlab_issue_sync.php >> /data/zentao/sync/cron.log 2>&1') | crontab -
```

Verify cron:

```bash
crontab -l | grep zentao_gitlab_issue_sync.php
systemctl is-active cron 2>/dev/null || systemctl is-active crond
tail -50 /data/zentao/sync/gitlab_issue_sync.log
```

## Conflict Handling

The script stores the last synced GitLab update time and ZenTao update time in `zt_gitlab_issue_sync`.

- If GitLab changed after the last sync and is newer than the ZenTao object, GitLab wins.
- If ZenTao changed after the last sync and is newer than the GitLab issue, ZenTao wins.
- When a GitLab issue changes from a bug label to a story label, the script creates a new ZenTao story and marks the previously synced ZenTao bug as deleted. The reverse migration also works for story to bug.

This is intentionally simple and predictable for a polling job. It does not sync comments.

## Security Notes

- Store the real config only on the ZenTao server.
- Restrict the config file to `600`.
- Use a GitLab token with the minimum project API permissions needed to read and update issues.
- The script disables TLS certificate verification for internal GitLab deployments with private certificates. If the GitLab CA is trusted by the ZenTao container, change `CURLOPT_SSL_VERIFYPEER` and `CURLOPT_SSL_VERIFYHOST` in the script.
