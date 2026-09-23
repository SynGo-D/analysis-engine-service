from analysis_engine.diffing.diff_parser import parse_numstat, parse_patch


def test_content_lines_that_look_like_headers_are_not_read_as_headers():
    # Removing "-- old" and adding "++ new" puts "--- old" and "+++ new"
    # inside the hunk. Read as headers, they would register a file named
    # "new".
    patch = b"""diff --git a/q.sql b/q.sql
index 1..2 100644
--- a/q.sql
+++ b/q.sql
@@ -2 +2 @@
--- old
+++ new
"""
    files = parse_patch(patch)

    assert set(files) == {"q.sql"}
    assert files["q.sql"].added_lines == [2]


def test_hunk_counts_default_to_one_and_zero_means_pure_deletion():
    patch = b"""diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -3 +3 @@
-a
+b
@@ -10,2 +9,0 @@
-c
-d
@@ -20,0 +18,3 @@
+e
+f
+g
"""
    x = parse_patch(patch)["x.py"]

    assert x.added_lines == [3, 18, 19, 20]
    assert x.deletion_points == [9]


def test_deletion_at_the_top_of_a_file_points_at_line_one():
    patch = b"""diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1 +0,0 @@
-first
"""
    assert parse_patch(patch)["x.py"].deletion_points == [1]


def test_deleted_file_is_keyed_by_its_old_path():
    patch = b"""diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-a
-b
"""
    gone = parse_patch(patch)["gone.py"]
    assert gone.status == "deleted"


def test_quoted_paths_with_octal_escapes_are_decoded():
    patch = b"""diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"
--- "a/caf\\303\\251.py"
+++ "b/caf\\303\\251.py"
@@ -0,0 +1 @@
+x
"""
    assert list(parse_patch(patch)) == ["café.py"]


def test_binary_file_is_registered_from_its_binary_line():
    patch = b"""diff --git a/logo.png b/logo.png
new file mode 100644
Binary files /dev/null and b/logo.png differ
"""
    logo = parse_patch(patch)["logo.png"]
    assert logo.is_binary and logo.status == "added"


def test_numstat_reads_renames_and_binary_files():
    output = b"3\t1\tapp/a.py\x000\t0\t\x00old name.py\x00new name.py\x00-\t-\tlogo.png\x00"
    entries = parse_numstat(output)

    assert [(e.path, e.old_path, e.lines_added, e.lines_removed) for e in entries] == [
        ("app/a.py", None, 3, 1),
        ("new name.py", "old name.py", 0, 0),
        ("logo.png", None, None, None),
    ]
    assert entries[2].is_binary
