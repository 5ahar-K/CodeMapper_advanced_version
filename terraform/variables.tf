variable "region" {
  type    = string
  default = "eu-north-1"
}

variable "my_ip_cidr" {
  type        = string
  description = "Your public IP as a /32, e.g. 203.0.113.5/32. Only this address can reach the server."
}

variable "public_key_path" {
  type    = string
  default = "~/.ssh/codemapper-key.pub"
}

variable "instance_type" {
  type        = string
  default     = "t3.micro"
  description = "Must be free-tier eligible in your project"
}

variable "disk_gb" {
  type    = number
  default = 20
}

variable "app_port" {
  type    = number
  default = 8000
}
