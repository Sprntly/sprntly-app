function CardHeader() { return <header><h2>Card Title</h2></header>; }
function CardBody()   { return <section><p>Card body text.</p></section>; }
function CardFooter() { return <footer><button>Action</button></footer>; }

export function NestedCard() {
  return (
    <article>
      <CardHeader />
      <CardBody />
      <CardFooter />
    </article>
  );
}
