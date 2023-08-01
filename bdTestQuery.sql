SELECT c.*, COUNT(m.id) as message_count
FROM chat c
LEFT JOIN message m ON c.id = m.chat_id
WHERE c.agent_id LIKE '%sartip@zauru.com%'
GROUP BY c.id
ORDER BY message_count DESC;

select * from message m where m.chat_id = 'RRB7EXPFFT'