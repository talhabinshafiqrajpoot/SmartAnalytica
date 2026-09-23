"""URL routes for SmartAnalytica."""

from django.urls import path

from . import views

urlpatterns = [
    # Public + auth
    path('', views.home, name='home'),
    path('register/', views.register, name='register'),
    path('login/', views.login_user, name='login'),
    path('logout/', views.logout_user, name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),

    # Teacher: courses
    path('teacher/', views.teacher_dashboard, name='teacher_dashboard'),
    path('courses/', views.manage_courses, name='manage_courses'),
    path('courses/new/', views.create_course, name='create_course'),
    path('courses/<int:course_id>/', views.course_details, name='course_details'),
    path('courses/<int:course_id>/edit/', views.edit_course, name='edit_course'),
    path('courses/<int:course_id>/delete/', views.delete_course, name='delete_course'),
    path('courses/enroll/', views.enroll_students, name='enroll_students'),

    # Teacher: assignments
    path('assignments/', views.view_assignments, name='view_assignments'),
    path('assignments/new/', views.create_assignment, name='create_assignment'),
    path('assignments/course/<int:course_id>/', views.view_assignments, name='course_assignments'),
    path('assignments/<int:assignment_id>/edit/', views.edit_assignment, name='edit_assignment'),
    path('assignments/<int:assignment_id>/delete/', views.delete_assignment, name='delete_assignment'),

    # Student
    path('student/', views.student_dashboard, name='student_dashboard'),
    path('student/submit/<int:assignment_id>/', views.submit_assignment, name='submit_assignment'),
    path('student/results/', views.my_results, name='my_results'),

    # Detection and reports
    path('reports/', views.plagiarism_reports, name='plagiarism_reports'),
    path('reports/run/<int:assignment_id>/', views.run_detection, name='run_detection'),
    path('reports/<int:report_id>/', views.report_detail, name='report_detail'),
    path('reports/match/<int:match_id>/', views.match_detail, name='match_detail'),

    # Administrator
    path('admin-panel/', views.admin_dashboard, name='admin_dashboard'),
    path('admin-panel/activity/', views.activity_log, name='activity_log'),
    path('admin-panel/integrity/', views.integrity_monitor, name='integrity_monitor'),

    # JSON
    path('api/courses/<int:course_id>/assignments/', views.get_assignments_for_course,
         name='get_assignments_for_course'),
]
