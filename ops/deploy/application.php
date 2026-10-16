<?php

// Run as the web-service account, never as root. Output contains no credentials.
if ( PHP_SAPI !== 'cli' || !isset( $argv[1], $argv[2] ) ||
    !in_array( $argv[2], [ 'inspect', 'upgrade', 'probe' ], true ) ) {
    fwrite( STDERR, "Invalid application-check invocation.\n" );
    exit( 1 );
}

$complete = false;
register_shutdown_function( function() use ( &$complete ) {
    if ( !$complete ) {
        fwrite( STDERR, "Application check did not complete.\n" );
        exit( 1 );
    }
} );

$root = realpath( $argv[1] );
if ( $root === false || !is_file( $root . '/user/config.php' ) ) {
    throw new RuntimeException( 'Application configuration is missing.' );
}
define( 'YOURLS_ABSPATH', $root );
define( 'YOURLS_CONFIGFILE', $root . '/user/config.php' );
define( 'YOURLS_FAST_INIT', true );
define( 'YOURLS_UPGRADING', true );
$_SERVER['HTTP_HOST'] = 'contra.dance';
$_SERVER['REQUEST_URI'] = '/';
require $root . '/includes/load-yourls.php';
yourls_get_all_options();

function release_snapshot(): array {
    $db = yourls_get_db( 'read-release-snapshot' );
    $plugins = yourls_get_option( 'active_plugins', [] );
    if ( !is_array( $plugins ) ) {
        throw new RuntimeException( 'Invalid active plugin configuration.' );
    }
    return [
        'version' => (string)yourls_get_option( 'version' ),
        'db_version' => (int)yourls_get_option( 'db_version' ),
        'links' => (int)$db->fetchValue( 'SELECT COUNT(*) FROM `' . YOURLS_DB_TABLE_URL . '`' ),
        'logs' => (int)$db->fetchValue( 'SELECT COUNT(*) FROM `' . YOURLS_DB_TABLE_LOG . '`' ),
        'plugins' => $plugins,
    ];
}

$before = release_snapshot();
if ( $before['db_version'] > (int)YOURLS_DB_VERSION ) {
    throw new RuntimeException( 'Database downgrade is forbidden.' );
}

if ( $argv[2] === 'upgrade' ) {
    ob_start();
    // The root-owned runner supplies maintenance; release trees remain read-only.
    if ( $before['db_version'] < (int)YOURLS_DB_VERSION ) {
        yourls_upgrade( 1, $before['version'], YOURLS_VERSION,
            $before['db_version'], YOURLS_DB_VERSION );
    }
    yourls_upgrade( 3, $before['version'], YOURLS_VERSION,
        $before['db_version'], YOURLS_DB_VERSION );
    ob_end_clean();
}

$after = release_snapshot();
if ( $argv[2] !== 'inspect' && (
    $after['db_version'] !== (int)YOURLS_DB_VERSION ||
    $after['version'] !== YOURLS_VERSION ||
    $after['links'] !== $before['links'] ||
    $after['logs'] !== $before['logs'] ||
    $after['plugins'] !== $before['plugins']
) ) {
    throw new RuntimeException( 'Release metadata or data-preservation check failed.' );
}

if ( $argv[2] === 'probe' ) {
    global $yourls_user_passwords;
    $site = rtrim( YOURLS_SITE, '/' );
    $url = parse_url( $site );
    $port = $url['port'] ?? ( $url['scheme'] === 'https' ? 443 : 80 );
    $timestamp = time();
    $signature = hash( 'sha256', $timestamp . yourls_auth_signature(
        (string)array_key_first( $yourls_user_passwords )
    ) );
    foreach ( [ '/', '/caller/', '/yourls-api.php' ] as $path ) {
        $request = curl_init( $site . $path );
        curl_setopt_array( $request, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_FOLLOWLOCATION => false,
            CURLOPT_CONNECTTIMEOUT => 5,
            CURLOPT_TIMEOUT => 20,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
            CURLOPT_RESOLVE => [ $url['host'] . ':' . $port . ':127.0.0.1' ],
        ] );
        if ( $path === '/yourls-api.php' ) {
            curl_setopt( $request, CURLOPT_POSTFIELDS, http_build_query( [
                'action' => 'stats', 'format' => 'json', 'filter' => 'top', 'limit' => 1,
                'timestamp' => $timestamp, 'signature' => $signature, 'hash' => 'sha256',
            ] ) );
        }
        $body = curl_exec( $request );
        $status = curl_getinfo( $request, CURLINFO_HTTP_CODE );
        if ( $body === false || $status !== 200 ) {
            throw new RuntimeException( 'Private HTTP probe failed: ' . $path );
        }
        if ( $path === '/' && !str_contains( $body, 'shortenlink' ) ) {
            throw new RuntimeException( 'Frontend form is missing.' );
        }
        if ( $path === '/caller/' && !str_contains( $body, 'password' ) ) {
            throw new RuntimeException( 'Caller authentication form is missing.' );
        }
        if ( $path === '/yourls-api.php' ) {
            $response = json_decode( $body, true, flags: JSON_THROW_ON_ERROR );
            if ( ( $response['status'] ?? null ) !== 'success' ) {
                throw new RuntimeException( 'Signed read-only API probe failed.' );
            }
        }
    }
}

$complete = true;
echo json_encode( $after, JSON_THROW_ON_ERROR ) . "\n";
