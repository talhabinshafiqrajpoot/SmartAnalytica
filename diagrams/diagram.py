from graphviz import Digraph

# Create a new directed graph
dfd = Digraph('DFD', filename='plagiarism_detector_dfd', format='png')

# Define entities
dfd.node('Student', shape='rectangle')
dfd.node('Teacher', shape='rectangle')
dfd.node('Database', shape='cylinder')

# Define processes
dfd.node('Register', shape='ellipse')
dfd.node('Login', shape='ellipse')
dfd.node('Upload Assignment', shape='ellipse')
dfd.node('Create Course', shape='ellipse')
dfd.node('Create Assignment', shape='ellipse')
dfd.node('Run Plagiarism Detection', shape='ellipse')
dfd.node('Notify Student', shape='ellipse')

# Define data stores
dfd.node('Assignments', shape='ellipse')
dfd.node('Courses', shape='ellipse')
dfd.node('Users', shape='ellipse')

# Define data flow
dfd.edge('Student', 'Register', label='Registration Data')
dfd.edge('Register', 'Database', label='New User Info')
dfd.edge('Student', 'Login', label='Login Credentials')
dfd.edge('Login', 'Database', label='Verify Credentials')
dfd.edge('Database', 'Login', label='Login Result')
dfd.edge('Login', 'Student', label='Login Status')

dfd.edge('Teacher', 'Create Course', label='Course Info')
dfd.edge('Create Course', 'Courses', label='New Course')
dfd.edge('Teacher', 'Create Assignment', label='Assignment Info')
dfd.edge('Create Assignment', 'Assignments', label='New Assignment')

dfd.edge('Student', 'Upload Assignment', label='Assignment Data')
dfd.edge('Upload Assignment', 'Assignments', label='Store Assignment')
dfd.edge('Teacher', 'Run Plagiarism Detection', label='Select Algorithm')
dfd.edge('Assignments', 'Run Plagiarism Detection', label='Assignments Data')
dfd.edge('Run Plagiarism Detection', 'Assignments', label='Plagiarism Results')

dfd.edge('Assignments', 'Notify Student', label='Marks Data')
dfd.edge('Notify Student', 'Student', label='Marks Notification')

# Render the diagram
dfd.view()
