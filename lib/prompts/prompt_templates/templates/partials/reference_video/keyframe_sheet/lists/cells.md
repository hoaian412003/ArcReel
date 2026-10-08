{% for cell in cells %}
Panel {{ cell.number }} (row {{ cell.row }}, column {{ cell.col }}):
  People: {{ cell.people }}
  Staging: {{ cell.staging }}
  Framing: {{ cell.framing }}
{% endfor %}
{% for cell in placeholders %}
Panel {{ cell.number }} (row {{ cell.row }}, column {{ cell.col }}): unused. Plain flat grey, nothing in it.
{% endfor %}