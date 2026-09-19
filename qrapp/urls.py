from django.urls import path
from . import views

urlpatterns = [
    # Mounted at /dashboard/ (not "") because qrproject/urls.py serves the
    # root with a redirect to login; mounting both at "" shadowed this view.
    path("dashboard/", views.dashboard_home, name="dashboard_home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("scanner/", views.scanner_view, name="scanner"),
    path("register/", views.register_view, name="register"),
    path("save_scan/", views.save_scan, name="save_scan"),
    path("approve_users/", views.approve_users, name="approve_users"),  # admin page

    path("admin_dashboard/", views.admin_dashboard, name="admin_dashboard"),  # OLD: Full dashboard with tables

    path("add_student/", views.add_student, name="add_student"),
    path("student/<int:student_id>/qr/", views.new_student_qr, name="new_student_qr"),
    path("student/<int:student_id>/qr/image/", views.student_qr_image, name="student_qr_image"),
    path("all_qr/", views.generate_all_qr, name="generate_all_qr"),
    path("upload_pdf/", views.upload_pdf, name="upload_pdf"),
    path("download_pdf/", views.download_qr_pdf, name="download_qr_pdf"),
    path("delete_all_qr/", views.delete_all_qr, name="delete_all_qr"),

    # Import issues: students a roster file could not identify (Students section)
    path("import_issues/", views.import_issues, name="import_issues"),
    path("import_issues/dismiss/<int:issue_id>/", views.dismiss_import_issue, name="dismiss_import_issue"),
    path("import_issues/export/", views.export_import_issues_pdf, name="export_import_issues_pdf"),
    path("import_issues/clear/", views.clear_import_issues, name="clear_import_issues"),

    # Student management
    path("edit_student/<int:student_id>/", views.edit_student, name="edit_student"),
    path("delete_student/<int:student_id>/", views.delete_student, name="delete_student"),


    path('ajax/student-list/', views.ajax_student_list, name='ajax_student_list'),
    path('ajax/dashboard-data/', views.ajax_dashboard_data, name='ajax_dashboard_data'),
    path('ajax/reports-data/', views.ajax_reports_data, name='ajax_reports_data'),
    path('export_students/', views.export_students, name='export_students'),
    path('export_attendance/', views.export_attendance, name='export_attendance'),
    path('ajax/qr-codes/', views.ajax_qr_codes, name='ajax_qr_codes'),
    path('export_qr_codes/', views.export_qr_codes, name='export_qr_codes'),
    
    # College management
    path('manage_colleges/', views.manage_colleges, name='manage_colleges'),
    path('ajax/get-colleges/', views.get_colleges_json, name='get_colleges_json'),
    path('ajax/get-programs/<str:college_code>/', views.get_programs_json, name='get_programs_json'),
    path('ajax/get-majors/<str:program_code>/', views.get_majors_json, name='get_majors_json'),
    path('manage_programs/<int:college_id>/', views.manage_programs, name='manage_programs'),

    # Event management
    path('manage_events/', views.manage_events, name='manage_events'),
    path('ajax/get-events/', views.get_events_json, name='get_events_json'),
    
    # User management
    path('ajax/manage-users/', views.manage_users, name='manage_users'),
    path('ajax/pending-users/', views.ajax_pending_users, name='ajax_pending_users'),
    path('ajax/scans-since/', views.scans_since, name='scans_since'),
    path('ajax/sidebar-logs/', views.ajax_sidebar_logs, name='ajax_sidebar_logs'),

]
