<?php
/**
 * Plugin Name: Register RankMath meta for REST
 * Description: Cho phép ghi rank_math_title / rank_math_description / rank_math_focus_keyword
 *              qua REST API (WordPress mặc định chặn các meta key chưa đăng ký show_in_rest).
 *              Bắt buộc để AutoContentPipeline ghim SEO title + meta description vào RankMath.
 *
 * CÀI ĐẶT: chép file này vào  wp-content/mu-plugins/  (tự tạo thư mục mu-plugins nếu chưa có).
 *          mu-plugins tự kích hoạt, không cần bật trong trang Plugins.
 */

add_action( 'init', function () {
    $keys = array(
        'rank_math_title',
        'rank_math_description',
        'rank_math_focus_keyword',
    );

    foreach ( $keys as $key ) {
        register_post_meta( 'post', $key, array(
            'type'          => 'string',
            'single'        => true,
            'show_in_rest'  => true,
            // Chỉ user có quyền sửa bài mới ghi được meta này qua REST.
            'auth_callback' => function () {
                return current_user_can( 'edit_posts' );
            },
        ) );
    }
} );
