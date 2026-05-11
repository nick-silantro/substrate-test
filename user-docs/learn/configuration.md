# Configuration

Substrate's built-in schema covers most needs, but you can customize it to fit your work exactly. You can hide types you don't use, rename things to match your vocabulary, add custom types, add custom attributes, and define new relationship kinds. All of it happens by asking your agent.

| What | How to ask your agent |
|------|-----------------------|
| Hide a type | "Hide the invoice type — I don't use it" |
| Alias a type | "Call `inquiry` a `question`" |
| Add a custom type | "Add a type called `client`" |
| Add a custom attribute | "Add a `budget` attribute to projects" |
| Add a custom relationship | "I want a `funds` relationship between projects" |
| View your customizations | "Show me my current customizations" |

Your agent stores display preferences in `_system/overlay.yaml` and schema extensions in `_system/schema-user/`. We don't recommend editing these files directly.

---

## When to customize

Not every difference in how you work needs a customization. Here's how to decide:

**Hide** when a built-in type is irrelevant to your work. If you're not a freelancer, hiding `invoice` keeps it out of suggestions without deleting anything. Existing entities of that type still exist and can still be found by name.

**Alias** when a built-in name doesn't match how you think. If you always think "question" instead of "inquiry," an alias makes the system match your vocabulary.

**Add a type** when you track something that genuinely doesn't fit any existing type — clients, recipes, research papers. If what you need is really just a note with a consistent label, use a note. If it has its own attributes, relationships, or lifecycle, it deserves a dedicated type.

**Add an attribute** when a type is missing an attribute you always want to track — budget on projects, deadline on documents, source on notes.

---

## Hiding types you don't use

Substrate ships with types for invoices, scripts, job opportunities, and more. Hide the ones that aren't relevant so they don't clutter suggestions and lists.

**Say to your agent:**
> "Hide the invoice type — I don't use it."

The type disappears from suggestions and type lists. Any existing invoice entities are unaffected and can still be found by name.

To bring a hidden type back:
> "Unhide the invoice type."

You can hide types, attributes, and relationships.

---

## Creating alternative names (aliases)

If a built-in name doesn't fit how you think, give it an alias. Both names work.

**Say to your agent:**
> "I want to call `inquiry` a `question`."

After that, "Create a question about the API design" works just like "Create an inquiry." The underlying type is still `inquiry`; you never have to use that word.

**Another example:**
> "I want to call `decision` a `conclusion`."

Aliases work for types, attributes, and relationships.

To remove an alias:
> "Remove my alias for inquiry."

**Note:** You cannot alias a name that already exists as a type. If there's a conflict, your agent will let you know.

---

## Adding your own types

If nothing in the built-in schema fits what you want to track, add a type.

**Say to your agent:**
> "Add a type called `recipe` for cooking recipes."

From then on, "Create a recipe for pasta carbonara" works like any built-in type. Custom types can be created, updated, linked to other entities, and queried. They survive engine updates — `substrate update` refreshes the built-in schema without touching your extensions.

---

## Adding custom attributes

To record information that existing types don't have an attribute for:

**Say to your agent:**
> "Add a `budget` attribute to projects."

After that, you can set and view a budget on any project, just like any built-in attribute. You can restrict an attribute to specific types or make it available on everything.

---

## Adding custom relationships

To define a new kind of connection between entities:

**Say to your agent:**
> "I want to be able to mark that a project `funds` another project."

Your agent adds the relationship and its inverse so both ends of the connection work correctly.

---

## Viewing what's in your schema

To see all available types (including your customizations, minus hidden ones):
> "Show me all entity types."

To see the attributes on a specific type:
> "What attributes does a project have?"

To review everything you've customized:
> "Show me my current customizations."
> "What types have I hidden?"

---

## What you cannot change

Built-in types, attributes, and relationships cannot be renamed or deleted — only hidden or aliased. This preserves compatibility when Substrate updates. If you need a different name, use an alias.

---

**Next:**
- [Entity Types](../reference/entity-types.md) — the full list of built-in types and when to use each
- [Skills Catalog](../reference/skills-catalog.md) — how your agent handles schema changes automatically
- [CLI Reference](../reference/cli.md) — the `substrate schema` commands if you want to run them directly
