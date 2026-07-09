SELECT indeks, prosek
FROM Student
WHERE prosek >= 7.5 AND prosek <= 9.5 AND godinaUpisa > 2019
  AND godinaUpisa < 2024 AND smer = 'RTI'
ORDER BY prosek;
