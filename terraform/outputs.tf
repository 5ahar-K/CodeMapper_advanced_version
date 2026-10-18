output "public_ip" {
  value = aws_instance.codemapper.public_ip
}

output "ssh_command" {
  value = "ssh -i ~/.ssh/codemapper-key ubuntu@${aws_instance.codemapper.public_ip}"
}
