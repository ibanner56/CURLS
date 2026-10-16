<?php

#[\PHPUnit\Framework\Attributes\Group('plugins')]
class URLHealthCheckTest extends PHPUnit\Framework\TestCase {

    private array $saved_filters;

    protected function setUp(): void {
        parent::setUp();
        $this->saved_filters = $GLOBALS['yourls_filters'];
        require_once YOURLS_ABSPATH . '/user/plugins/url-health-check/plugin.php';
        yourls_remove_all_filters( 'shunt_add_new_link' );
        yourls_add_filter( 'shunt_add_new_link', 'url_health_check' );
    }

    protected function tearDown(): void {
        $GLOBALS['yourls_filters'] = $this->saved_filters;
        parent::tearDown();
    }

    public function test_allowed_url_preserves_continue_sentinel(): void {
        $result = yourls_apply_filter(
            'shunt_add_new_link', yourls_shunt_default(), 'ftp://example.test/file'
        );

        $this->assertSame( yourls_shunt_default(), $result );
    }

    public function test_earlier_plugin_rejection_is_preserved(): void {
        $rejection = [
            'status' => 'fail',
            'code' => 'error:spam',
            'message' => 'Blocked by an earlier plugin',
        ];
        yourls_add_filter( 'shunt_add_new_link', function() use ( $rejection ) {
            return $rejection;
        }, 5 );

        $this->assertSame( $rejection, yourls_apply_filter(
            'shunt_add_new_link', yourls_shunt_default(), 'not a valid URL'
        ) );
    }

    public function test_legitimate_false_shunt_is_preserved(): void {
        yourls_add_filter( 'shunt_add_new_link', 'yourls_return_false', 5 );

        $this->assertFalse( yourls_apply_filter(
            'shunt_add_new_link', yourls_shunt_default(), 'not a valid URL'
        ) );
    }

    public function test_invalid_url_is_still_rejected(): void {
        $result = yourls_apply_filter(
            'shunt_add_new_link', yourls_shunt_default(), 'not a valid URL'
        );

        $this->assertIsArray( $result );
        $this->assertSame( 'fail', $result['status'] );
        $this->assertSame( 'error:invalid', $result['code'] );
        $this->assertSame( '400', $result['statusCode'] );
    }

    #[\PHPUnit\Framework\Attributes\Group('network')]
    public function test_trusted_https_preserves_continue_sentinel(): void {
        $this->assertSame( yourls_shunt_default(), yourls_apply_filter(
            'shunt_add_new_link', yourls_shunt_default(), 'https://example.com/'
        ) );
    }

    #[\PHPUnit\Framework\Attributes\Group('network')]
    public function test_self_signed_https_is_rejected(): void {
        $url = 'https://self-signed.badssl.com/';
        $probe = curl_init( $url );
        curl_setopt_array( $probe, [
            CURLOPT_NOBODY => true,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
            CURLOPT_CONNECTTIMEOUT => 3,
            CURLOPT_TIMEOUT => 3,
        ] );
        curl_exec( $probe );
        $error = curl_errno( $probe );
        unset( $probe );
        $this->assertSame(
            CURLE_SSL_CACERT, $error,
            'The HTTPS fixture must fail certificate verification, not DNS or connectivity.'
        );

        $result = yourls_apply_filter( 'shunt_add_new_link', yourls_shunt_default(), $url );

        $this->assertIsArray( $result );
        $this->assertSame( 'fail', $result['status'] );
        $this->assertSame( 'error:url', $result['code'] );
    }
}
