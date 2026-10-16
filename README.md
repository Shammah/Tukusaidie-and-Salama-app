# Tukusaidie and Salama App

Two mobile-first web apps for Kenyan businesses.

- **Tukusaidie** (`tukusaidie/`): an IT helpdesk. Customers register their business, raise a ticket and see the price only after the ticket is raised, then pay by M-Pesa prompt or till number. Service agents sign up with their speciality and certifications and see only the tickets assigned to them. An owner-only admin side manages tickets, prices and agents.
- **Salama App** (`salama/`): a cybersecurity self-check survey.

## Run

Each app is a single self-contained `index.html` with no build step. Open it in a browser, or enable GitHub Pages (Settings → Pages → Deploy from branch → `main` / root) and visit `/tukusaidie/` or `/salama/`.

Data is kept in the browser's `localStorage`, so each device keeps its own records.
