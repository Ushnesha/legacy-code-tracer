package Shapes is
   type Shape is abstract tagged record
      Id : Integer := 0;
   end record;

   procedure Draw (S : Shape) is abstract;
   function Area (S : Shape) return Integer is abstract;

   type Circle is new Shape with record
      Radius : Integer := 1;
   end record;
   overriding procedure Draw (S : Circle);
   overriding function Area (S : Circle) return Integer;

   type Square is new Shape with record
      Side : Integer := 1;
   end record;
   overriding procedure Draw (S : Square);
   overriding function Area (S : Square) return Integer;

   procedure Draw_All (S : Shape'Class);
end Shapes;
