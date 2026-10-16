<?php

if ( getenv( 'GITHUB_ACTIONS' ) !== 'true' ||
    getenv( 'RUNNER_ENVIRONMENT' ) !== 'github-hosted' ||
    !preg_match( '/^[a-f0-9]{64}$/', getenv( 'CURLS_CI_PASSWORD' ) ?: '' ) ) {
    throw new RuntimeException( 'This database fixture is restricted to hosted CI.' );
}

$port = filter_var( getenv( 'DB_PORT' ), FILTER_VALIDATE_INT );
if ( !$port || $port < 1024 || $port > 65535 ) {
    throw new RuntimeException( 'Invalid isolated database port.' );
}
$db = new PDO( "mysql:host=127.0.0.1;port=$port", 'root',
    getenv( 'CURLS_CI_ROOT_PASSWORD' ), [ PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION ] );
foreach ( [ 'yourls_tests', 'curls_upgrade' ] as $name ) {
    $db->exec( "CREATE DATABASE `$name` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" );
}
$password = $db->quote( getenv( 'CURLS_CI_PASSWORD' ) );
$db->exec( "CREATE USER 'curls_ci'@'%' IDENTIFIED BY $password" );
$db->exec( "GRANT ALL ON yourls_tests.* TO 'curls_ci'@'%'" );
$db->exec( "GRANT ALL ON curls_upgrade.* TO 'curls_ci'@'%'" );
