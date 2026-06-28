"""
Document Drive: plain Python authorization with Zanzibar primitives
===================================================================

Read this one first. It keeps the application tiny and uses the in-memory
repository so the authorization model is visible:

- users join groups;
- folders are shared with users or group member sets;
- documents inherit folder viewers through ``parent->viewer``;
- explicit bans win over inherited access;
- reverse lookup answers "which documents can Bob view?" without your app
  scanning every document.

The point is not the document-drive domain. The point is the shape: product
permissions are relationship facts plus a small schema that explains how those
facts compose.

Run:
    uv run python examples/document_drive.py
"""

from zanzipy.client import ZanzibarClient
from zanzipy.models import Relation
from zanzipy.schema import (
    ComputedUsersetRule,
    ExclusionRule,
    NamespaceDef,
    PermissionDef,
    RelationDef,
    SchemaRegistry,
    SubjectReference,
    TupleToUsersetRule,
    UnionRule,
)
from zanzipy.storage.repos import InMemoryRelationRepository

# === Schema Definitions ======================================================

# user namespace: principals
user_ns = NamespaceDef(name="user")

# group namespace: classic group membership
group_ns = NamespaceDef(
    name="group",
    relations=(
        # Who are the members of this group?
        RelationDef(
            name="member",
            allowed_subjects=SubjectReference(namespace="user"),
        ),
    ),
)

# folder namespace: owners, editors, viewers; nested parents; bans
folder_ns = NamespaceDef(
    name="folder",
    relations=(
        # Direct relations only (no rewrite specified -> direct tuples)
        RelationDef(
            name="owner",
            allowed_subjects=(
                SubjectReference(namespace="user"),
                SubjectReference(namespace="group", relation=Relation("member")),
            ),
        ),
        RelationDef(
            name="editor",
            allowed_subjects=(
                SubjectReference(namespace="user"),
                SubjectReference(namespace="group", relation=Relation("member")),
            ),
        ),
        RelationDef(
            name="viewer",
            allowed_subjects=(
                SubjectReference(namespace="user"),
                SubjectReference(namespace="group", relation=Relation("member")),
            ),
        ),
        # Optional ban list for exclusion examples
        RelationDef(
            name="banned",
            allowed_subjects=SubjectReference(namespace="user"),
        ),
        # Nested hierarchy: folder can have a parent folder
        RelationDef(
            name="parent",
            allowed_subjects=SubjectReference(namespace="folder"),
            description="Optional parent folder for nested inheritance.",
        ),
    ),
    permissions=(
        # Folder viewing: owners/editors/viewers, plus parent.viewer via
        # tuple-to-userset; exclude any user explicitly banned on this folder
        PermissionDef(
            name="can_view",
            rewrite=ExclusionRule(
                base=UnionRule(
                    children=(
                        ComputedUsersetRule("owner"),
                        ComputedUsersetRule("editor"),
                        ComputedUsersetRule("viewer"),
                        TupleToUsersetRule(
                            tuple_relation="parent",
                            computed_relation="viewer",
                        ),
                    )
                ),
                subtract=ComputedUsersetRule("banned"),
            ),
            description=(
                "Folder viewers include owners, editors, direct viewers, and parent "
                "folder.viewer subjects; explicit bans take precedence."
            ),
        ),
        # Edit requires owner or editor
        PermissionDef(
            name="can_edit",
            rewrite=UnionRule(
                children=(
                    ComputedUsersetRule("owner"),
                    ComputedUsersetRule("editor"),
                )
            ),
        ),
    ),
)

# document namespace: documents live inside folders and include folder.viewer
document_ns = NamespaceDef(
    name="document",
    relations=(
        RelationDef(
            name="owner",
            allowed_subjects=(
                SubjectReference(namespace="user"),
                SubjectReference(namespace="group", relation=Relation("member")),
            ),
        ),
        RelationDef(
            name="editor",
            allowed_subjects=(
                SubjectReference(namespace="user"),
                SubjectReference(namespace="group", relation=Relation("member")),
            ),
        ),
        RelationDef(
            name="viewer",
            allowed_subjects=(
                SubjectReference(namespace="user"),
                SubjectReference(namespace="group", relation=Relation("member")),
            ),
        ),
        # Optional ban list on document
        RelationDef(
            name="banned",
            allowed_subjects=SubjectReference(namespace="user"),
        ),
        # Each document belongs to a folder via a direct relation to the folder object
        RelationDef(
            name="parent",
            allowed_subjects=SubjectReference(namespace="folder"),
            description="Containing folder for this document.",
        ),
    ),
    permissions=(
        # Inherit viewing from the folder's viewer relation via tuple-to-userset
        PermissionDef(
            name="can_view",
            rewrite=ExclusionRule(
                base=UnionRule(
                    children=(
                        ComputedUsersetRule("owner"),
                        ComputedUsersetRule("editor"),
                        ComputedUsersetRule("viewer"),
                        # Follow parent (document->folder) then folder.viewer
                        TupleToUsersetRule(
                            tuple_relation="parent",
                            computed_relation="viewer",
                        ),
                    )
                ),
                subtract=ComputedUsersetRule("banned"),
            ),
            description=(
                "Document viewers include owners/editors/viewers and subjects on the "
                "containing folder's viewer relation (via parent->viewer). Explicit "
                "bans on the document take precedence."
            ),
        ),
        PermissionDef(
            name="can_edit",
            rewrite=UnionRule(
                children=(
                    ComputedUsersetRule("owner"),
                    ComputedUsersetRule("editor"),
                )
            ),
            description="Document editing does not inherit from folder (by design).",
        ),
    ),
)


# Register schema in the registry
registry = SchemaRegistry()
registry.register_many((user_ns, group_ns, folder_ns, document_ns))


# === Client =================================================================

# In-memory storage keeps the example zero-dependency. The client API is the
# same when you swap in SQLAlchemy-backed storage later.
client = ZanzibarClient(
    schema=registry,
    relations_repository=InMemoryRelationRepository(),
    enable_debug=False,
)


# === Helper functions ========================================================


def add_group_member(group_id: str, user_id: str) -> None:
    """Add a user to a group's "member" relation.

    This powers subject-set expansion when the group is used as a subject, e.g.
    assigning "group:eng#member" to folder.viewer makes all group members viewers.
    """

    client.write(f"group:{group_id}", "member", f"user:{user_id}")


def create_folder(folder_id: str, owner: str) -> None:
    """Create a folder and set its owner (user or group)."""

    client.write(f"folder:{folder_id}", "owner", owner)


def share_folder_viewer_with_group(folder_id: str, group_id: str) -> None:
    """Grant a group viewer rights on a folder via subject set.

    We attach the subject set "group:{group_id}#member" as a folder.viewer.
    All members of that group become folder viewers.
    """

    client.write(f"folder:{folder_id}", "viewer", f"group:{group_id}#member")


def create_document(doc_id: str, owner: str, parent_folder_id: str) -> None:
    """Create a document, set its owner, and link to its parent folder.

    The parent link enables tuple-to-userset traversal to folder.viewer.
    """

    client.write(f"document:{doc_id}", "owner", owner)
    client.write(f"document:{doc_id}", "parent", f"folder:{parent_folder_id}")


def share_document_editor_with_user(doc_id: str, user_id: str) -> None:
    """Grant a user editor rights directly on a document."""

    client.write(f"document:{doc_id}", "editor", f"user:{user_id}")


def ban_document_viewer(doc_id: str, user_id: str) -> None:
    """Deny a user even when another path would otherwise allow viewing."""

    client.write(f"document:{doc_id}", "banned", f"user:{user_id}")


def can_view_folder(folder_id: str, user_id: str) -> bool:
    return client.check(f"folder:{folder_id}", "can_view", f"user:{user_id}")


def can_view_doc(doc_id: str, user_id: str) -> bool:
    return client.check(f"document:{doc_id}", "can_view", f"user:{user_id}")


def can_edit_doc(doc_id: str, user_id: str) -> bool:
    return client.check(f"document:{doc_id}", "can_edit", f"user:{user_id}")


def list_docs_user_can_view(user_id: str) -> list[str]:
    """Enumerate documents a user can view.

    Uses the rewrite-aware reverse LookupResources path behind
    ``client.list_objects`` rather than scanning candidate documents and
    checking them one by one.
    """

    return client.list_objects("document", "can_view", f"user:{user_id}")


# === Scenario ================================================================

print("=== Document Drive: folders, documents, and sharing ===\n")

# Actors
users = ["alice", "bob", "charlie", "dora", "eve"]
group = "eng"

# Onboarding: add Bob and Charlie to the engineering group
add_group_member(group, "bob")
add_group_member(group, "charlie")

print("Group setup:")
print("- Added bob and charlie as members of group:eng\n")

# Create a project folder owned by Alice and share with the eng group (viewer)
create_folder("proj", owner="user:alice")
share_folder_viewer_with_group("proj", group)

print("Folder created and shared:")
print("- folder:proj owner -> user:alice")
print("- folder:proj viewer -> group:eng#member (all members can view)\n")

# Create a document under the folder; Alice owns it. Link it to the folder.
create_document("spec", owner="user:alice", parent_folder_id="proj")

print("Document created:")
print("- document:spec owner -> user:alice")
print("- document:spec parent -> folder:proj (enables doc view via folder.viewer)\n")

# Grant Dora edit rights directly, then ban Charlie on this document.
# Charlie is still in group:eng, so the later False result proves that
# exclusions beat inherited group access.
share_document_editor_with_user("spec", "dora")
ban_document_viewer("spec", "charlie")

print("Sharing:")
print("- granted editor on document:spec to user:dora")
print("- banned user:charlie on document:spec despite group inheritance\n")

# Checks: who can view/edit?
print("=== Checks (why they pass/fail) ===")
print("Folder viewing:")
print(f"- Alice (owner) can view folder: {can_view_folder('proj', 'alice')}")
print(f"- Bob (eng member) can view folder via group: {can_view_folder('proj', 'bob')}")
print(f"- Eve (not shared) can view folder: {can_view_folder('proj', 'eve')}\n")

print("Document viewing:")
print(f"- Alice (owner) can view doc: {can_view_doc('spec', 'alice')}")
print(
    f"- Bob (via folder viewer inheritance) can view doc: {can_view_doc('spec', 'bob')}"
)
print(
    "- Charlie (eng member, but explicitly banned) can view doc: "
    f"{can_view_doc('spec', 'charlie')}"
)
print(f"- Dora (editor) can view doc: {can_view_doc('spec', 'dora')}")
print(f"- Eve (not shared) can view doc: {can_view_doc('spec', 'eve')}\n")

print("Document editing:")
print(f"- Alice (owner) can edit doc: {can_edit_doc('spec', 'alice')}")
print(f"- Dora (editor) can edit doc: {can_edit_doc('spec', 'dora')}")
print(
    "- Bob (folder viewer only) can edit doc (should be False): "
    f"{can_edit_doc('spec', 'bob')}\n"
)

print("=== Enumerations ===")
print("Documents each user can view (computed):")
for u in users:
    print(f"- {u}: {list_docs_user_can_view(u)}")

print("\nNotes:")
print(
    "- Folder viewers are owners, editors, or direct viewers; using a group "
    "subject set makes all group members viewers."
)
print(
    "- Documents include their folder's viewer subjects (parent->viewer) but "
    "not editing; this demonstrates tuple-to-userset and a deliberate "
    "non-inheritance choice. Explicit document bans subtract from the final "
    "viewer set."
)

# === Expansion (who can X?) ==================================================

print("\n=== Expansion (who can?) ===")
fold_view = client.expand("folder:proj", "can_view")
print("folder:proj can_view ->")
print(f"  users:    {sorted(fold_view.users)}")
print(f"  usersets: {sorted(fold_view.usersets)}")

doc_view = client.expand("document:spec", "can_view")
print("document:spec can_view ->")
print(f"  users:    {sorted(doc_view.users)}")
print(f"  usersets: {sorted(doc_view.usersets)}")
