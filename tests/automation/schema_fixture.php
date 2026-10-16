<?php

if ( PHP_SAPI !== 'cli' || getenv( 'GITHUB_ACTIONS' ) !== 'true' ||
    getenv( 'RUNNER_ENVIRONMENT' ) !== 'github-hosted' || !isset( $argv[1], $argv[2] ) ||
    !in_array( $argv[2], [ '506', 'future', 'restore' ], true ) ) {
    throw new RuntimeException( 'Schema failure injection is restricted to hosted CI.' );
}
define( 'YOURLS_ABSPATH', realpath( $argv[1] ) );
define( 'YOURLS_CONFIGFILE', YOURLS_ABSPATH . '/user/config.php' );
define( 'YOURLS_FAST_INIT', true );
define( 'YOURLS_UPGRADING', true );
require YOURLS_ABSPATH . '/includes/load-yourls.php';
if ( YOURLS_DB_NAME !== 'curls_upgrade' || YOURLS_DB_USER !== 'curls_ci' ) {
    throw new RuntimeException( 'Refusing a non-fixture database.' );
}
yourls_get_all_options();
if ( $argv[2] === '506' ) {
    if ( (int)YOURLS_DB_VERSION !== 507 ) {
        throw new RuntimeException( 'The 506 fixture targets the known 507 index migration.' );
    }
    yourls_get_db( 'write-ci-old-index' )->perform(
        'ALTER TABLE `' . YOURLS_DB_TABLE_URL . '` DROP INDEX url_idx'
    );
    yourls_update_option( 'db_version', '506' );
} else {
    yourls_update_option( 'db_version',
        (string)( (int)YOURLS_DB_VERSION + ( $argv[2] === 'future' ? 1 : 0 ) ) );
}
echo "Synthetic schema injection completed.\n";
