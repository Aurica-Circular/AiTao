# Contributing to AiTao

Thanks for your interest in AiTao. Contributions are welcome — bug reports, fixes,
documentation, translations, extractors, test cases.

## Before you start

- **Open an issue first** for anything larger than a bug fix. It saves you from building
  something the project does not want, and it saves the maintainer from reviewing it.
- Documentation inside this repository is written in **English**.
- Code conventions are described in [docs/](docs/). Keep files under ~350 lines, add a
  header comment to every new file, and write tests.

## Contributor terms — and why AiTao needs them

AiTao is dual-licensed: the Core is free software under AGPL-3.0, and separate Premium
Modules are sold commercially (see [LICENSING.md](LICENSING.md)). That business model
funds the project's development.

This creates a practical problem. When you contribute code, **you keep the copyright on
what you wrote**. Contributing it under AGPL-3.0 lets the project use it in AiTao Core —
but it does *not* let the maintainer include it in a commercially licensed product. If a
contribution ends up in a paid module without that permission, the contributor can
rightfully object, and untangling shared code months later is painful for everyone.

So AiTao asks every contributor to certify one extra point on top of the standard
Developer Certificate of Origin. **You keep your copyright and your moral rights.** You
grant a licence, not an assignment.

### The grant

By signing off a commit (see below), you certify the [Developer Certificate of Origin
1.1](https://developercertificate.org/) — in short: you wrote the code, or you have the
right to submit it — **and, in addition**, you agree to the following:

> **Rights granted.** You grant Philippe BERTIERI, maintainer and copyright holder of
> AiTao, a non-exclusive, royalty-free licence over your contribution, covering each of the
> following rights separately:
>
> a) the right to reproduce your contribution, permanently or temporarily, in whole or in
>    part, by any means and in any form, including as part of a larger work;
>
> b) the right to translate, adapt, arrange or otherwise modify your contribution, and to
>    reproduce the results;
>
> c) the right to distribute your contribution and any modified form of it to the public,
>    for consideration or free of charge, by any means, including by making it available
>    online.
>
> **Purpose and scope of exploitation.** These rights are granted for the following two
> purposes, and no others: (i) distribution of AiTao Core, and of works derived from it,
> under the GNU Affero General Public License version 3; and (ii) distribution of
> commercially licensed AiTao products, including proprietary AiTao Premium Modules, under
> terms chosen by the maintainer.
>
> **Territory.** Worldwide.
>
> **Duration.** For the full duration of the copyright in your contribution, including any
> extension.
>
> **What you keep.** You remain the author and copyright holder of your contribution.
> This is a licence, not a transfer of ownership. Your moral rights — in particular the
> right to be recognised as the author — are unaffected. You remain entirely free to use,
> publish and licence your contribution elsewhere, however you wish.
>
> **Sub-licensing.** The maintainer may sub-license these rights to an entity he controls —
> currently Aurica Circular Co. Ltd. (歐瑞卡有限公司) — for the purpose of distributing
> AiTao products, and to recipients of AiTao Core and of AiTao Premium Modules as part of
> those works.
>
> **Warranty.** You provide your contribution "as is", with no warranty of any kind.
>
> **Governing law.** These contributor terms are governed by the laws of Taiwan
> (Republic of China).

### How to certify

Add a `Signed-off-by` line to each of your commits:

```
git commit -s -m "fix: correct page ordering in OCR output"
```

which appends:

```
Signed-off-by: Your Name <your.email@example.com>
```

A check runs on every pull request and reports any commit that is not signed off, with
the command to fix it. If you are not comfortable with the terms above, say so in the
pull request — a bug report or a reproduction case is a valuable contribution too, and
requires no sign-off.

## Pull request checklist

- [ ] Commits are signed off (`git commit -s`)
- [ ] Tests added or updated, and the suite passes
- [ ] `ruff check src/ tests/` reports no error — this is what CI enforces
- [ ] No `print()` in `src/` — use the logger
- [ ] The pull request describes *why*, not only *what*

## Reporting a security issue

Please do **not** open a public issue. See [SECURITY.md](SECURITY.md).
