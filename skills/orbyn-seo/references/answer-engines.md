# Pages that answer engines can quote

AI answers and featured snippets split a page into heading-bounded sections and
quote one that answers on its own. Write each section so it can be lifted out alone.

- **Answer first.** State the direct answer in the opening sentences, before
  background. On a list or roundup, name the top pick there.
- **Question headings.** Phrase at least one heading as the question people ask,
  and follow it with a one or two sentence answer before code, tables or detail.
  Headings in order, no skipped levels, sentence case.
- **Self-contained comparison blocks.** When a page compares items, give each the
  same block: name, one-line verdict, best for, strengths, trade-offs, price. A
  section quoted alone must not attach one product's price to another.
- **Facts as text.** Prices, limits and specs in the HTML as plain text, matching
  the structured data ([page metadata](../../orbyn-ui-design/references/page-metadata.md)).
- **One number, one source.** The same count or price everywhere on the site, from
  one constant.
- **Name entities plainly:** the product, company and what it is, the way people
  search for it, with the former name after a rebrand.
- **`llms.txt` is optional.** If the site has one, generate it from the same data as
  the pages so it never disagrees with them; it does not replace crawlable pages.
- **Specific over generic.** First-hand numbers, dated tests and real examples are
  what answers quote; filler intros and repeated boilerplate are not. For prose
  clean-up, use [unslop](../../unslop/SKILL.md).

Being cited takes time and is outside the site's control: report the structure you
shipped, never a citation you have not observed.
