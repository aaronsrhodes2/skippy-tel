# Cost alarm: a monthly budget on the project that emails at 50%, 90% and 100% of the amount.
#
# Created only when var.billing_account is set (the Captain supplies the id; nothing here guesses it), so a plain
# `terraform apply` without it changes nothing and needs no billing permission. To turn it on:
#   1. set the repository variable BILLING_ACCOUNT on GitHub (Settings > Secrets and variables > Actions >
#      Variables): deploy.yml passes it in as TF_VAR_billing_account; or put billing_account in terraform.tfvars;
#   2. give the deployer (skippy-deployer@<project>.iam.gserviceaccount.com) permission to manage budgets on that
#      billing account: "Billing Account Costs Manager" (roles/billing.costsManager, which holds billing.budgets.*),
#      granted on the billing account itself, not on the project: Billing > Account management > Add principal.
# See docs/cicd.md and docs/games/README.md.
#
# The budget emails the billing account's own administrators and users by default. `budget_alert_email` adds one more
# address through a Cloud Monitoring email channel.

locals {
  budget_on = var.billing_account != ""
}

data "google_project" "this" {
  count      = local.budget_on ? 1 : 0
  project_id = var.project
}

resource "google_project_service" "billingbudgets" {
  count              = local.budget_on ? 1 : 0
  service            = "billingbudgets.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "monitoring" {
  count              = local.budget_on && var.budget_alert_email != "" ? 1 : 0
  service            = "monitoring.googleapis.com"
  disable_on_destroy = false
}

resource "google_monitoring_notification_channel" "budget_email" {
  count        = local.budget_on && var.budget_alert_email != "" ? 1 : 0
  display_name = "${var.name} budget alerts"
  type         = "email"
  labels = {
    email_address = var.budget_alert_email
  }
  depends_on = [google_project_service.monitoring]
}

resource "google_billing_budget" "games" {
  count           = local.budget_on ? 1 : 0
  billing_account = var.billing_account
  display_name    = "${var.name} monthly budget"

  budget_filter {
    projects = ["projects/${data.google_project.this[0].number}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.budget_monthly_usd)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }

  dynamic "all_updates_rule" {
    for_each = var.budget_alert_email != "" ? [1] : []
    content {
      monitoring_notification_channels = [google_monitoring_notification_channel.budget_email[0].id]
      # Keep the default recipients (billing admins and users) as well as the extra address.
      disable_default_iam_recipients = false
    }
  }

  depends_on = [google_project_service.billingbudgets]
}
