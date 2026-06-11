<?php
declare(strict_types=1);

$configFile = __DIR__ . '/zentao_gitlab_issue_sync.config.php';
if (!is_file($configFile)) {
    fwrite(STDERR, "Missing config: {$configFile}\n");
    exit(2);
}

$cfg = require $configFile;
$cfg += [
    'bug_labels'      => ['bug', '缺陷'],
    'story_labels'    => ['feature', 'enhancement', 'story', '需求'],
    'default_type'    => 'bug',
    'progress_labels' => ['status:wait', 'status:doing', 'status:done', 'status:closed'],
];

function logLine(string $message): void
{
    global $cfg;
    $line = date('c') . ' ' . $message . PHP_EOL;
    file_put_contents($cfg['log_file'], $line, FILE_APPEND);
    echo $line;
}

function db(): mysqli
{
    global $cfg;
    static $db = null;
    if ($db instanceof mysqli) return $db;
    mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);
    $db = new mysqli($cfg['db_host'], $cfg['db_user'], $cfg['db_password'], $cfg['db_name'], (int)$cfg['db_port']);
    $db->set_charset('utf8mb4');
    return $db;
}

function q(string $sql, string $types = '', mixed ...$params): mysqli_stmt
{
    $stmt = db()->prepare($sql);
    if ($types !== '') $stmt->bind_param($types, ...$params);
    $stmt->execute();
    return $stmt;
}

function one(string $sql, string $types = '', mixed ...$params): ?array
{
    $stmt = q($sql, $types, ...$params);
    $row = $stmt->get_result()->fetch_assoc();
    return $row ?: null;
}

function all(string $sql, string $types = '', mixed ...$params): array
{
    $stmt = q($sql, $types, ...$params);
    return $stmt->get_result()->fetch_all(MYSQLI_ASSOC);
}

function gitlab(string $method, string $path, array $query = [], ?array $body = null): array
{
    global $cfg;
    $query['private_token'] = $cfg['gitlab_token'];
    $url = rtrim($cfg['gitlab_url'], '/') . '/api/v4' . $path . '?' . http_build_query($query);
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CUSTOMREQUEST  => $method,
        CURLOPT_SSL_VERIFYPEER => false,
        CURLOPT_SSL_VERIFYHOST => false,
        CURLOPT_TIMEOUT        => 60,
        CURLOPT_HTTPHEADER     => ['Content-Type: application/json'],
    ]);
    if ($body !== null) curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body, JSON_UNESCAPED_UNICODE));
    $raw = curl_exec($ch);
    $errno = curl_errno($ch);
    $error = curl_error($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    if ($errno) throw new RuntimeException("GitLab curl error {$errno}: {$error}");
    $data = $raw === '' ? null : json_decode((string)$raw, true);
    if ($status < 200 || $status >= 300) {
        $safe = preg_replace('/glpat-[A-Za-z0-9_.-]+/', '[REDACTED]', (string)$raw);
        throw new RuntimeException("GitLab {$method} {$path} failed HTTP {$status}: {$safe}");
    }
    return is_array($data) ? $data : [];
}

function dt(?string $value): int
{
    if (!$value || $value === '0000-00-00 00:00:00') return 0;
    return strtotime($value) ?: 0;
}

function limitTitle(string $title): string
{
    return function_exists('mb_substr') ? mb_substr($title, 0, 255) : substr($title, 0, 255);
}

function htmlToText(?string $html): string
{
    $text = html_entity_decode(strip_tags((string)$html), ENT_QUOTES | ENT_HTML5, 'UTF-8');
    return trim(preg_replace("/\n{3,}/", "\n\n", str_replace(["\r\n", "\r"], "\n", $text)));
}

function issueBody(array $issue): string
{
    $desc = trim((string)($issue['description'] ?? ''));
    $body = nl2br(htmlspecialchars($desc, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'), false);
    if (!empty($issue['web_url'])) {
        $url = htmlspecialchars((string)$issue['web_url'], ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
        $body .= "<br><br><a href=\"{$url}\" target=\"_blank\">{$url}</a>";
    }
    return $body . '<br><br>[gitlab-iid:' . (int)$issue['iid'] . ']';
}

function issueTextFromZenTao(array $row, string $type): string
{
    $field = $type === 'story' ? 'spec' : 'steps';
    $body = preg_replace('/\[gitlab-iid:\d+\]/', '', htmlToText($row[$field] ?? ''));
    $label = $type === 'story' ? 'ZenTao Story' : 'ZenTao Bug';
    return trim(trim((string)$body) . "\n\nSynced from {$label} #{$row['id']}");
}

function lowerLabels(array $labels): array
{
    return array_map(fn($v) => mb_strtolower((string)$v, 'UTF-8'), $labels);
}

function hasAnyLabel(array $labels, array $needles): bool
{
    $lower = lowerLabels($labels);
    foreach ($needles as $needle) {
        $n = mb_strtolower((string)$needle, 'UTF-8');
        foreach ($lower as $label) {
            if ($label === $n || str_starts_with($label, $n . ':')) return true;
        }
    }
    return false;
}

function classifyIssue(array $issue, ?array $sync = null): string
{
    global $cfg;
    $labels = $issue['labels'] ?? [];
    if (hasAnyLabel($labels, $cfg['bug_labels'])) return 'bug';
    if (hasAnyLabel($labels, $cfg['story_labels'])) return 'story';
    return ($sync['zentao_type'] ?? $cfg['default_type']) === 'story' ? 'story' : 'bug';
}

function progressFromIssue(array $issue): string
{
    $labels = lowerLabels($issue['labels'] ?? []);
    foreach (['status:closed', 'progress:closed'] as $label) if (in_array($label, $labels, true)) return 'closed';
    foreach (['status:done', 'progress:done', 'status:finish', 'progress:finish'] as $label) if (in_array($label, $labels, true)) return 'done';
    foreach (['status:doing', 'progress:doing', 'status:in-progress', 'progress:in-progress'] as $label) if (in_array($label, $labels, true)) return 'doing';
    foreach (['status:wait', 'progress:wait', 'status:todo', 'progress:todo'] as $label) if (in_array($label, $labels, true)) return 'wait';
    return (($issue['state'] ?? '') === 'closed') ? 'closed' : 'doing';
}

function bugStatusFromProgress(string $progress): string
{
    return in_array($progress, ['done', 'closed'], true) ? 'closed' : 'active';
}

function storyStatusFromProgress(string $progress): string
{
    return $progress === 'closed' ? 'closed' : 'active';
}

function storyStageFromProgress(string $progress): string
{
    return match ($progress) {
        'doing' => 'developing',
        'done' => 'developed',
        'closed' => 'closed',
        default => 'wait',
    };
}

function progressFromBug(array $bug): string
{
    return (($bug['status'] ?? '') === 'closed' || ($bug['status'] ?? '') === 'resolved') ? 'closed' : 'doing';
}

function progressFromStory(array $story): string
{
    if (($story['status'] ?? '') === 'closed') return 'closed';
    return match ($story['stage'] ?? '') {
        'developing' => 'doing',
        'developed', 'testing', 'tested', 'released' => 'done',
        default => 'wait',
    };
}

function labelsForGitlab(array $existing, string $type, string $progress): array
{
    global $cfg;
    $drop = array_merge($cfg['progress_labels'], ['progress:wait', 'progress:doing', 'progress:done', 'progress:closed']);
    $labels = [];
    foreach ($existing as $label) {
        $lower = mb_strtolower((string)$label, 'UTF-8');
        if (in_array($lower, $drop, true)) continue;
        if ($type === 'bug' && in_array($lower, ['feature', 'enhancement', 'story', '需求'], true)) continue;
        if ($type === 'story' && in_array($lower, ['bug', '缺陷'], true)) continue;
        $labels[] = (string)$label;
    }
    $labels[] = $type === 'bug' ? 'bug' : 'feature';
    $labels[] = 'status:' . ($progress === 'closed' ? 'closed' : $progress);
    return array_values(array_unique($labels));
}

function objectTimestamp(array $row, string $type): int
{
    if ($type === 'story') {
        return max(dt($row['lastEditedDate'] ?? null), dt($row['closedDate'] ?? null), dt($row['changedDate'] ?? null), dt($row['openedDate'] ?? null));
    }
    return max(dt($row['lastEditedDate'] ?? null), dt($row['closedDate'] ?? null), dt($row['resolvedDate'] ?? null), dt($row['openedDate'] ?? null));
}

function ensureTables(): void
{
    q("CREATE TABLE IF NOT EXISTS zt_gitlab_issue_sync (
        id INT UNSIGNED NOT NULL AUTO_INCREMENT,
        gitlab_id INT UNSIGNED NOT NULL,
        gitlab_project_id INT UNSIGNED NOT NULL,
        gitlab_iid INT UNSIGNED NOT NULL,
        gitlab_issue_id INT UNSIGNED NOT NULL DEFAULT 0,
        zentao_type VARCHAR(30) NOT NULL DEFAULT 'bug',
        zentao_id INT UNSIGNED NOT NULL,
        product INT UNSIGNED NOT NULL,
        project INT UNSIGNED NOT NULL,
        execution INT UNSIGNED NOT NULL,
        gitlab_updated_at DATETIME NULL,
        zentao_updated_at DATETIME NULL,
        last_direction VARCHAR(20) NOT NULL DEFAULT '',
        last_error TEXT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        PRIMARY KEY(id),
        UNIQUE KEY gitlab_issue (gitlab_id, gitlab_project_id, gitlab_iid),
        UNIQUE KEY zentao_object (zentao_type, zentao_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
}

function ensureRelation(string $type, int $objectID, int $issueIID): void
{
    global $cfg;
    q(
        "INSERT IGNORE INTO zt_relation(project, product, execution, AType, AID, AVersion, relation, BType, BID, BVersion, extra)
         VALUES (?, ?, ?, ?, ?, 1, 'gitlab', 'issue', ?, ?, ?)",
        'iiisiiis',
        $cfg['zentao_project_id'],
        $cfg['zentao_product_id'],
        $cfg['zentao_execution_id'],
        $type,
        $objectID,
        $issueIID,
        $cfg['gitlab_project_id'],
        $cfg['gitlab_id']
    );
}

function upsertSync(string $type, int $iid, int $issueID, int $objectID, string $gitlabUpdated, string $zentaoUpdated, string $direction, ?string $error = null): void
{
    global $cfg;
    q(
        "INSERT INTO zt_gitlab_issue_sync(gitlab_id, gitlab_project_id, gitlab_iid, gitlab_issue_id, zentao_type, zentao_id,
          product, project, execution, gitlab_updated_at, zentao_updated_at, last_direction, last_error, created_at, updated_at)
         VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())
         ON DUPLICATE KEY UPDATE gitlab_issue_id=VALUES(gitlab_issue_id), zentao_type=VALUES(zentao_type), zentao_id=VALUES(zentao_id),
          gitlab_updated_at=VALUES(gitlab_updated_at), zentao_updated_at=VALUES(zentao_updated_at),
          last_direction=VALUES(last_direction), last_error=VALUES(last_error), updated_at=NOW()",
        'iiiisiiiissss',
        $cfg['gitlab_id'],
        $cfg['gitlab_project_id'],
        $iid,
        $issueID,
        $type,
        $objectID,
        $cfg['zentao_product_id'],
        $cfg['zentao_project_id'],
        $cfg['zentao_execution_id'],
        $gitlabUpdated,
        $zentaoUpdated,
        $direction,
        $error
    );
}

function createBugFromIssue(array $issue): int
{
    global $cfg;
    $opened = date('Y-m-d H:i:s', dt($issue['created_at'] ?? null) ?: time());
    $progress = progressFromIssue($issue);
    $status = bugStatusFromProgress($progress);
    $closed = $status === 'closed' ? date('Y-m-d H:i:s', dt($issue['closed_at'] ?? $issue['updated_at'] ?? null) ?: time()) : null;
    q(
        "INSERT INTO zt_bug(project, product, execution, title, severity, pri, type, steps, status, confirmed,
         openedBy, openedDate, openedBuild, assignedTo, assignedDate, closedBy, closedDate, issueKey, deleted)
         VALUES (?, ?, ?, ?, 3, 3, 'codeerror', ?, ?, 1, ?, ?, 'trunk', ?, ?, ?, ?, ?, 0)",
        'iiissssssssss',
        $cfg['zentao_project_id'],
        $cfg['zentao_product_id'],
        $cfg['zentao_execution_id'],
        limitTitle((string)$issue['title']),
        issueBody($issue),
        $status,
        $cfg['zentao_actor'],
        $opened,
        $cfg['zentao_actor'],
        $opened,
        $status === 'closed' ? $cfg['zentao_actor'] : '',
        $closed,
        'gitlab:' . $cfg['gitlab_project_id'] . '#' . (int)$issue['iid']
    );
    $id = (int)db()->insert_id;
    ensureRelation('bug', $id, (int)$issue['iid']);
    action('bug', $id, 'ImportFromGitlab', (string)$issue['iid']);
    return $id;
}

function updateBugFromIssue(int $id, array $issue): void
{
    global $cfg;
    $status = bugStatusFromProgress(progressFromIssue($issue));
    $closed = $status === 'closed' ? date('Y-m-d H:i:s', dt($issue['closed_at'] ?? $issue['updated_at'] ?? null) ?: time()) : null;
    q(
        "UPDATE zt_bug SET title=?, steps=?, status=?, closedBy=?, closedDate=?, lastEditedBy=?, lastEditedDate=NOW() WHERE id=?",
        'ssssssi',
        limitTitle((string)$issue['title']),
        issueBody($issue),
        $status,
        $status === 'closed' ? $cfg['zentao_actor'] : '',
        $closed,
        $cfg['zentao_actor'],
        $id
    );
    action('bug', $id, 'edited', 'Synced from GitLab issue #' . (int)$issue['iid']);
}

function createStoryFromIssue(array $issue): int
{
    global $cfg;
    $opened = date('Y-m-d H:i:s', dt($issue['created_at'] ?? null) ?: time());
    $progress = progressFromIssue($issue);
    $status = storyStatusFromProgress($progress);
    $stage = storyStageFromProgress($progress);
    $closed = $status === 'closed' ? date('Y-m-d H:i:s', dt($issue['closed_at'] ?? $issue['updated_at'] ?? null) ?: time()) : null;
    q(
        "INSERT INTO zt_story(product, branch, title, type, category, pri, status, stage, openedBy, openedDate, assignedTo, assignedDate,
          closedBy, closedDate, closedReason, version, vision, deleted)
         VALUES(?, 0, ?, 'story', 'feature', 3, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'rnd', 0)",
        'issssssssss',
        $cfg['zentao_product_id'],
        limitTitle((string)$issue['title']),
        $status,
        $stage,
        $cfg['zentao_actor'],
        $opened,
        $cfg['zentao_actor'],
        $opened,
        $status === 'closed' ? $cfg['zentao_actor'] : '',
        $closed,
        $status === 'closed' ? 'done' : ''
    );
    $id = (int)db()->insert_id;
    q("UPDATE zt_story SET root=?, path=? WHERE id=?", 'isi', $id, ",{$id},", $id);
    q("INSERT INTO zt_storyspec(story, version, title, spec, verify) VALUES(?, 1, ?, ?, '')",
        'iss', $id, limitTitle((string)$issue['title']), issueBody($issue));
    q("INSERT IGNORE INTO zt_projectstory(project, product, branch, story, version) VALUES(?, ?, 0, ?, 1)",
        'iii', $cfg['zentao_project_id'], $cfg['zentao_product_id'], $id);
    q("INSERT IGNORE INTO zt_projectstory(project, product, branch, story, version) VALUES(?, ?, 0, ?, 1)",
        'iii', $cfg['zentao_execution_id'], $cfg['zentao_product_id'], $id);
    ensureRelation('story', $id, (int)$issue['iid']);
    action('story', $id, 'ImportFromGitlab', (string)$issue['iid']);
    return $id;
}

function updateStoryFromIssue(int $id, array $issue): void
{
    global $cfg;
    $progress = progressFromIssue($issue);
    $status = storyStatusFromProgress($progress);
    $stage = storyStageFromProgress($progress);
    $closed = $status === 'closed' ? date('Y-m-d H:i:s', dt($issue['closed_at'] ?? $issue['updated_at'] ?? null) ?: time()) : null;
    q(
        "UPDATE zt_story SET title=?, status=?, stage=?, closedBy=?, closedDate=?, closedReason=?, lastEditedBy=?, lastEditedDate=NOW() WHERE id=?",
        'sssssssi',
        limitTitle((string)$issue['title']),
        $status,
        $stage,
        $status === 'closed' ? $cfg['zentao_actor'] : '',
        $closed,
        $status === 'closed' ? 'done' : '',
        $cfg['zentao_actor'],
        $id
    );
    q("UPDATE zt_storyspec SET title=?, spec=? WHERE story=? AND version=1",
        'ssi', limitTitle((string)$issue['title']), issueBody($issue), $id);
    action('story', $id, 'edited', 'Synced from GitLab issue #' . (int)$issue['iid']);
}

function action(string $type, int $id, string $action, string $extra = ''): void
{
    global $cfg;
    q("INSERT INTO zt_action(objectType, objectID, product, project, execution, actor, action, date, comment, extra, vision)
       VALUES(?, ?, ?, ?, ?, ?, ?, NOW(), '', ?, 'rnd')",
       'siiiisss', $type, $id, $cfg['zentao_product_id'], $cfg['zentao_project_id'], $cfg['zentao_execution_id'], $cfg['zentao_actor'], $action, $extra);
}

function fetchIssues(): array
{
    global $cfg;
    $issues = [];
    for ($page = 1; $page <= 50; $page++) {
        $batch = gitlab('GET', "/projects/{$cfg['gitlab_project_id']}/issues", ['state' => 'all', 'per_page' => 100, 'page' => $page]);
        if (!$batch) break;
        foreach ($batch as $issue) $issues[(int)$issue['iid']] = $issue;
        if (count($batch) < 100) break;
    }
    return $issues;
}

function loadObject(string $type, int $id): ?array
{
    if ($type === 'story') {
        return one("SELECT s.*, ss.spec FROM zt_story s LEFT JOIN zt_storyspec ss ON ss.story=s.id AND ss.version=s.version WHERE s.id=? AND s.deleted=0", 'i', $id);
    }
    return one("SELECT * FROM zt_bug WHERE id=? AND deleted=0", 'i', $id);
}

function migrateTypeIfNeeded(array $sync, string $wantedType, array $issue): array
{
    global $cfg;
    if (($sync['zentao_type'] ?? '') === $wantedType) return $sync;
    if ($wantedType === 'story') {
        $newID = createStoryFromIssue($issue);
        if (($sync['zentao_type'] ?? '') === 'bug') q("UPDATE zt_bug SET deleted=1 WHERE id=?", 'i', (int)$sync['zentao_id']);
        upsertSync('story', (int)$issue['iid'], (int)$issue['id'], $newID, date('Y-m-d H:i:s', dt($issue['updated_at'] ?? null) ?: time()), date('Y-m-d H:i:s'), 'gitlab_to_zentao');
        logLine("migrated GitLab issue #{$issue['iid']} from bug to ZenTao story #{$newID}");
        return one("SELECT * FROM zt_gitlab_issue_sync WHERE gitlab_id=? AND gitlab_project_id=? AND gitlab_iid=?",
            'iii', $cfg['gitlab_id'], $cfg['gitlab_project_id'], (int)$issue['iid']);
    }
    $newID = createBugFromIssue($issue);
    if (($sync['zentao_type'] ?? '') === 'story') q("UPDATE zt_story SET deleted=1 WHERE id=?", 'i', (int)$sync['zentao_id']);
    upsertSync('bug', (int)$issue['iid'], (int)$issue['id'], $newID, date('Y-m-d H:i:s', dt($issue['updated_at'] ?? null) ?: time()), date('Y-m-d H:i:s'), 'gitlab_to_zentao');
    logLine("migrated GitLab issue #{$issue['iid']} from story to ZenTao bug #{$newID}");
    return one("SELECT * FROM zt_gitlab_issue_sync WHERE gitlab_id=? AND gitlab_project_id=? AND gitlab_iid=?",
        'iii', $cfg['gitlab_id'], $cfg['gitlab_project_id'], (int)$issue['iid']);
}

function syncObjectToGitlab(array $row, string $type, ?int $iid = null, ?int $issueID = null, array $existingLabels = []): void
{
    global $cfg;
    $progress = $type === 'story' ? progressFromStory($row) : progressFromBug($row);
    $body = [
        'title'       => (string)($type === 'story' ? $row['title'] : $row['title']),
        'description' => issueTextFromZenTao($row, $type),
        'labels'      => implode(',', labelsForGitlab($existingLabels, $type, $progress)),
    ];
    if ($progress === 'closed') $body['state_event'] = 'close';
    elseif ($iid) $body['state_event'] = 'reopen';

    if ($iid) {
        $issue = gitlab('PUT', "/projects/{$cfg['gitlab_project_id']}/issues/{$iid}", [], $body);
    } else {
        $issue = gitlab('POST', "/projects/{$cfg['gitlab_project_id']}/issues", [], $body);
        $iid = (int)$issue['iid'];
        $issueID = (int)$issue['id'];
        ensureRelation($type, (int)$row['id'], $iid);
        if ($type === 'bug') q("UPDATE zt_bug SET issueKey=? WHERE id=?", 'si', 'gitlab:' . $cfg['gitlab_project_id'] . '#' . $iid, (int)$row['id']);
        logLine("created GitLab issue #{$iid} from ZenTao {$type} #{$row['id']}");
    }
    upsertSync($type, (int)$iid, (int)($issueID ?: ($issue['id'] ?? 0)), (int)$row['id'],
        date('Y-m-d H:i:s', dt($issue['updated_at'] ?? null) ?: time()),
        date('Y-m-d H:i:s', objectTimestamp($row, $type)),
        'zentao_to_gitlab');
}

function syncGitlabToZentao(array $issues): void
{
    global $cfg;
    foreach ($issues as $iid => $issue) {
        try {
            $sync = one("SELECT * FROM zt_gitlab_issue_sync WHERE gitlab_id=? AND gitlab_project_id=? AND gitlab_iid=?",
                'iii', $cfg['gitlab_id'], $cfg['gitlab_project_id'], $iid);
            $type = classifyIssue($issue, $sync);
            $gitlabUpdated = date('Y-m-d H:i:s', dt($issue['updated_at'] ?? null) ?: time());
            if (!$sync) {
                $id = $type === 'story' ? createStoryFromIssue($issue) : createBugFromIssue($issue);
                $row = loadObject($type, $id);
                upsertSync($type, $iid, (int)$issue['id'], $id, $gitlabUpdated, date('Y-m-d H:i:s', objectTimestamp($row, $type)), 'gitlab_to_zentao');
                logLine("imported GitLab issue #{$iid} as ZenTao {$type} #{$id}");
                continue;
            }
            $sync = migrateTypeIfNeeded($sync, $type, $issue);
            $row = loadObject($type, (int)$sync['zentao_id']);
            if (!$row) continue;
            $gitlabTs = dt($issue['updated_at'] ?? null);
            $storedGitlabTs = dt($sync['gitlab_updated_at'] ?? null);
            $objectTs = objectTimestamp($row, $type);
            $storedObjectTs = dt($sync['zentao_updated_at'] ?? null);
            if ($gitlabTs > $storedGitlabTs && $gitlabTs >= $objectTs) {
                $type === 'story' ? updateStoryFromIssue((int)$row['id'], $issue) : updateBugFromIssue((int)$row['id'], $issue);
                $row = loadObject($type, (int)$row['id']);
                upsertSync($type, $iid, (int)$issue['id'], (int)$row['id'], $gitlabUpdated, date('Y-m-d H:i:s', objectTimestamp($row, $type)), 'gitlab_to_zentao');
                logLine("updated ZenTao {$type} #{$row['id']} from GitLab issue #{$iid}");
            } elseif ($objectTs > $storedObjectTs && $objectTs > $gitlabTs) {
                syncObjectToGitlab($row, $type, $iid, (int)$issue['id'], $issue['labels'] ?? []);
            } else {
                $desired = labelsForGitlab($issue['labels'] ?? [], $type, $type === 'story' ? progressFromStory($row) : progressFromBug($row));
                if ($desired !== ($issue['labels'] ?? [])) {
                    gitlab('PUT', "/projects/{$cfg['gitlab_project_id']}/issues/{$iid}", [], ['labels' => implode(',', $desired)]);
                }
            }
        } catch (Throwable $e) {
            logLine("error syncing GitLab issue #{$iid}: " . $e->getMessage());
        }
    }
}

function syncNewZentaoObjects(string $type): void
{
    global $cfg;
    if ($type === 'story') {
        $rows = all("SELECT s.*, ss.spec FROM zt_story s LEFT JOIN zt_storyspec ss ON ss.story=s.id AND ss.version=s.version
            LEFT JOIN zt_gitlab_issue_sync x ON x.zentao_type='story' AND x.zentao_id=s.id
            WHERE s.product=? AND s.deleted=0 AND x.id IS NULL", 'i', $cfg['zentao_product_id']);
    } else {
        $rows = all("SELECT b.* FROM zt_bug b LEFT JOIN zt_gitlab_issue_sync x ON x.zentao_type='bug' AND x.zentao_id=b.id
            WHERE b.product=? AND b.execution=? AND b.deleted=0 AND x.id IS NULL", 'ii', $cfg['zentao_product_id'], $cfg['zentao_execution_id']);
    }
    foreach ($rows as $row) {
        try {
            syncObjectToGitlab($row, $type);
        } catch (Throwable $e) {
            logLine("error creating GitLab issue from ZenTao {$type} #{$row['id']}: " . $e->getMessage());
        }
    }
}

ensureTables();
logLine('sync started');
$issues = fetchIssues();
syncGitlabToZentao($issues);
syncNewZentaoObjects('story');
syncNewZentaoObjects('bug');
logLine('sync finished');
