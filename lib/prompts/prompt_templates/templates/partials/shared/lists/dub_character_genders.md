{% for entry in characters %}
- {{ entry.name }}：{{ entry.gender or "未登记" }}
{% endfor %}
