module.exports = {
  apps : [{
    name   : "discord-bot",
    script : "./main.py",
    interpreter: "./venv/bin/python",
    watch: true,
    ignore_watch: ["node_modules", "venv", "__pycache__", "*.log"],
  }]
}
