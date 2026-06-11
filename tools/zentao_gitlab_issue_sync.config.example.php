<?php
declare(strict_types=1);

return [
    'db_host' => '127.0.0.1',
    'db_port' => 3306,
    'db_name' => 'zentao',
    'db_user' => 'root',
    'db_password' => 'change-me',

    'gitlab_url' => 'https://gitlab.example.com',
    'gitlab_token' => 'glpat-change-me',
    'gitlab_id' => 1,
    'gitlab_project_id' => 78,

    'zentao_product_id' => 1,
    'zentao_project_id' => 1,
    'zentao_execution_id' => 2,
    'zentao_actor' => 'admin',

    'log_file' => '/data/sync/gitlab_issue_sync.log',

    'bug_labels' => ['bug', '缺陷'],
    'story_labels' => ['feature', 'enhancement', 'story', '需求'],
    'default_type' => 'bug',
    'progress_labels' => ['status:wait', 'status:doing', 'status:done', 'status:closed'],
];
