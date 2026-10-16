<?php

if ( PHP_SAPI !== 'cli' || getenv( 'GITHUB_ACTIONS' ) !== 'true' ||
    getenv( 'RUNNER_ENVIRONMENT' ) !== 'github-hosted' || !isset( $argv[1] ) ) {
    throw new RuntimeException( 'Synthetic installation is restricted to hosted CI.' );
}
$complete = false;
register_shutdown_function( function() use ( &$complete ) {
    if ( !$complete ) {
        fwrite( STDERR, "Synthetic installation failed.\n" );
        exit( 1 );
    }
} );
define( 'YOURLS_ABSPATH', realpath( $argv[1] ) );
define( 'YOURLS_CONFIGFILE', YOURLS_ABSPATH . '/user/config.php' );
define( 'YOURLS_FAST_INIT', true );
define( 'YOURLS_INSTALLING', true );
require YOURLS_ABSPATH . '/includes/load-yourls.php';
if ( YOURLS_DB_NAME !== 'curls_upgrade' || YOURLS_DB_USER !== 'curls_ci' ) {
    throw new RuntimeException( 'Refusing a non-fixture database.' );
}
// Fast init skips the plugins_loaded hook that normally initializes sanitizers.
yourls_kses_init();
$result = yourls_create_sql_tables();
if ( $result['error'] ) {
    throw new RuntimeException( 'Synthetic installation returned errors.' );
}
yourls_get_all_options();
$result = yourls_add_new_link(
    'https://example.test/preserved', 'cipreserved', 'Preserved synthetic link'
);
if ( $result['status'] !== 'success' ) {
    throw new RuntimeException( 'Synthetic link creation failed.' );
}
yourls_get_db( 'write-ci-log' )->perform(
    'INSERT INTO `' . YOURLS_DB_TABLE_LOG . '` ' .
    '(click_time,shorturl,referrer,user_agent,ip_address,country_code) ' .
    "VALUES ('2026-01-01 00:00:00','cipreserved','direct','CI','127.0.0.1','US')"
);
yourls_update_option( 'active_plugins', [
    'url-health-check/plugin.php',
    'hyphens-in-urls/plugin.php',
    'force-lowercase/plugin.php',
] );
$complete = true;
echo "Synthetic baseline installed.\n";
