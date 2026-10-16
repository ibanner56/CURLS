<?php

// Keep these plugin tests independent of application configuration and database installation.
if ( PHP_SAPI !== 'cli' || defined( 'YOURLS_CONFIGFILE' ) ) {
    throw new RuntimeException( 'Run URL Health Check tests in a fresh CLI process without the application bootstrap.' );
}

// PHPUnit includes bootstrap files inside a function.
global $yourls_filters, $yourls_actions, $yourls_l10n;

define( 'YOURLS_ABSPATH', dirname( __DIR__, 2 ) );

require_once YOURLS_ABSPATH . '/includes/vendor/autoload.php';
require_once YOURLS_ABSPATH . '/includes/functions-plugins.php';
require_once YOURLS_ABSPATH . '/includes/functions.php';
require_once YOURLS_ABSPATH . '/includes/functions-l10n.php';
